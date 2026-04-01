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
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse(
                "{"
                '"queue": {'
                '"pending": 4, "in_progress": 2, "failed": 1'
                "}"
                "}"
            )
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
            return _FakeResponse('{"vram_used_mb": 6144, "vram_total_mb": 12288}')
        if url.endswith("/api/v1/models"):
            return _FakeResponse('{"loaded_count": 2}')
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
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse('{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}')
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
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse('{"queue": {"pending": 1}}')
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


async def test_invokeai_getter_falls_back_from_html_endpoints(monkeypatch) -> None:
    """Getter should skip HTML fallback pages and use newer InvokeAI endpoints."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        body_map = {
            "/api/v1/queue/default/status": (
                '{"queue": {"pending": 3, "in_progress": 1, "failed": 2}}'
            ),
            "/api/v1/queue/items": "<!DOCTYPE html><html><body>app</body></html>",
            "/api/v1/queue/default/list_all": (
                '{"items": [{"item_id": "abc", "status": "completed"}]}'
            ),
            "/api/v1/app/version": '{"version": "6.12.0.post1"}',
            "/api/v1/system/stats": "<!DOCTYPE html><html><body>app</body></html>",
            "/api/v2/models/stats": (
                '{"high_watermark": 7340032000, "cache_size": 21474836480, '
                '"loaded_model_sizes": {"a": 1, "b": 2, "c": 3}}'
            ),
            "/api/v1/models": "<!DOCTYPE html><html><body>app</body></html>",
        }

        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.12.0.post1"
    assert payload["invokeai.queue.pending_count"] == 3.0
    assert payload["invokeai.queue.in_progress_count"] == 1.0
    assert payload["invokeai.queue.failed_count"] == 2.0
    assert payload["invokeai.last_job.id"] == "abc"
    assert payload["invokeai.last_job.status"] == "completed"
    assert payload["invokeai.models.loaded_count"] == 3.0
    assert payload["invokeai.system.vram_used_mb"] == 7000.0
    assert payload["invokeai.system.vram_total_mb"] == 20480.0


async def test_invokeai_getter_tolerates_optional_timeout(monkeypatch) -> None:
    """Timeouts on optional enrichment endpoints should not fail the getter."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse('{"queue": {"pending": 2, "in_progress": 1, "failed": 0}}')
        if url.endswith("/api/v1/queue/items"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v1/queue/default/list_all"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v1/app/version"):
            return _FakeResponse('{"version": "6.12.0.post1"}')
        if url.endswith("/api/v1/system/stats"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse('{"loaded_model_sizes": {"a": 1}}')
        if url.endswith("/api/v1/models"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.12.0.post1"
    assert payload["invokeai.queue.pending_count"] == 2.0
    assert payload["invokeai.queue.in_progress_count"] == 1.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.last_job.id"] == ""
    assert payload["invokeai.models.loaded_count"] == 1.0


async def test_invokeai_getter_tolerates_queue_status_failure(monkeypatch) -> None:
    """Queue-status endpoint failure should still emit non-queue InvokeAI data."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/default/status"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v1/queue/status"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v1/queue/items"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v1/queue/default/list_all"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v1/app/version"):
            return _FakeResponse('{"version": "6.12.0.post1"}')
        if url.endswith("/api/v1/system/stats"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse('{"loaded_model_sizes": {"a": 1, "b": 2}}')
        if url.endswith("/api/v1/models"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.12.0.post1"
    assert payload["invokeai.queue.pending_count"] == 0.0
    assert payload["invokeai.queue.in_progress_count"] == 0.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.models.loaded_count"] == 2.0
