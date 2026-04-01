"""Tests for :mod:`casedd.getters.invokeai`."""

from __future__ import annotations

from urllib.error import HTTPError

from casedd.data_store import DataStore
from casedd.getters.invokeai import InvokeAIGetter


class _FakeResponse:
    """Minimal context-managed HTTP response for urlopen monkeypatching."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


async def test_invokeai_getter_active_queue(monkeypatch) -> None:
    """InvokeAI getter should flatten queue and last-job values."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        auth_header = req.get_header("Authorization")
        assert auth_header == "Bearer token-123"
        if url.endswith("/api/v1/queue/status"):
            return _FakeResponse('{"pending_count": 4, "in_progress_count": 2, "failed_count": 1}')
        if url.endswith("/api/v1/queue/items"):
            return _FakeResponse(

                    "{"
                    '"items": ['
                    '{"id": "job-42", "status": "completed", "model_name": "sdxl", '
                    '"width": 1024, "height": 768, '
                    '"completed_at": "2026-03-31T11:22:33Z"}'
                    "]"
                    "}"

            )
        if url.endswith("/api/v1/app/version"):
            return _FakeResponse('{"version": "5.4.0"}')
        if url.endswith("/api/v1/system/stats"):
            return _FakeResponse('{"gpu": {"vram_used_mb": 6144, "vram_total_mb": 12288}}')
        if url.endswith("/api/v1/models"):
            return _FakeResponse('{"models": [{"name": "sdxl"}, {"name": "flux"}]}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(
        DataStore(),
        base_url="http://bandit:9090",
        api_token="token-123",
    )
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "5.4.0"
    assert payload["invokeai.queue.pending_count"] == 4.0
    assert payload["invokeai.queue.in_progress_count"] == 2.0
    assert payload["invokeai.queue.failed_count"] == 1.0
    assert payload["invokeai.last_job.id"] == "job-42"
    assert payload["invokeai.last_job.status"] == "completed"
    assert payload["invokeai.last_job.model"] == "sdxl"
    assert payload["invokeai.last_job.width"] == 1024.0
    assert payload["invokeai.last_job.height"] == 768.0
    assert payload["invokeai.last_job.completed_at"] == "2026-03-31T11:22:33Z"
    assert payload["invokeai.system.vram_used_mb"] == 6144.0
    assert payload["invokeai.system.vram_total_mb"] == 12288.0
    assert payload["invokeai.models.loaded_count"] == 2.0


async def test_invokeai_getter_idle_queue(monkeypatch) -> None:
    """Idle queue should still return stable defaults for last-job metadata."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/status"):
            return _FakeResponse('{"pending_count": 0, "in_progress_count": 0, "failed_count": 0}')
        if url.endswith("/api/v1/queue/items"):
            return _FakeResponse('{"items": []}')
        if url.endswith("/api/v1/app/version"):
            return _FakeResponse('{"version": "5.4.0"}')
        if url.endswith("/api/v1/system/stats"):
            return _FakeResponse("{}")
        if url.endswith("/api/v1/models"):
            return _FakeResponse('{"loaded_count": 0}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.queue.pending_count"] == 0.0
    assert payload["invokeai.queue.in_progress_count"] == 0.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.last_job.id"] == ""
    assert payload["invokeai.last_job.status"] == ""
    assert payload["invokeai.last_job.model"] == ""
    assert payload["invokeai.last_job.width"] == 0.0
    assert payload["invokeai.last_job.height"] == 0.0
    assert payload["invokeai.models.loaded_count"] == 0.0


async def test_invokeai_getter_auth_failure(monkeypatch) -> None:
    """HTTP auth failures should return placeholder values gracefully."""

    def _raise_auth(req, timeout: float, context=None):
        raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _raise_auth)

    getter = InvokeAIGetter(DataStore(), api_token="bad")
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "—"
    assert payload["invokeai.queue.pending_count"] == 0.0
    assert payload["invokeai.last_job.id"] == "—"
    assert payload["invokeai.last_job.model"] == "—"
    assert payload["invokeai.models.loaded_count"] == 0.0


async def test_invokeai_getter_partial_metadata(monkeypatch) -> None:
    """Partial payloads should be accepted with defaults for missing fields."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/status"):
            return _FakeResponse('{"pending_count": 1}')
        if url.endswith("/api/v1/queue/items"):
            return _FakeResponse(
                '{"items": [{"id": "job-1", "status": "running", "model": {"name": "flux"}}]}'
            )
        if url.endswith("/api/v1/app/version"):
            return _FakeResponse("{}")
        if url.endswith("/api/v1/system/stats"):
            return _FakeResponse('{"system": {"vram_total_mb": 24576}}')
        if url.endswith("/api/v1/models"):
            return _FakeResponse('{"results": [{"name": "flux"}]}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == ""
    assert payload["invokeai.queue.pending_count"] == 1.0
    assert payload["invokeai.queue.in_progress_count"] == 0.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.last_job.id"] == "job-1"
    assert payload["invokeai.last_job.status"] == "running"
    assert payload["invokeai.last_job.model"] == "flux"
    assert payload["invokeai.last_job.width"] == 0.0
    assert payload["invokeai.last_job.height"] == 0.0
    assert payload["invokeai.system.vram_used_mb"] == 0.0
    assert payload["invokeai.system.vram_total_mb"] == 24576.0
    assert payload["invokeai.models.loaded_count"] == 1.0
