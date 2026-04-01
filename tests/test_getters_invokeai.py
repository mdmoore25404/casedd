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
    """InvokeAI getter should flatten queue, current item, and preview values."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        auth_header = req.get_header("Authorization")
        assert auth_header == "Bearer token-123"
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse(
                "{"
                '"queue": {'
                '"pending": 4, "in_progress": 2, "failed": 1'
                '}, "processor": {"is_started": true, "is_processing": true}'
                "}"
            )
        if url.endswith("/api/v1/queue/default/current"):
            return _FakeResponse(
                "{"
                '"item_id": 42, "status": "in_progress", '
                '"model": {"name": "sdxl"}, '
                '"field_values": {"width": 1024, "height": 768}, '
                '"started_at": "2026-03-31T11:22:33Z"'
                "}"
            )
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse(
                '{"cache_size": 12884901888, '
                '"loaded_model_sizes": {"unet": 5368709120, "vae": 1073741824}}'
            )
        if url.endswith("/api/v1/images/names"):
            return _FakeResponse('{"image_names": ["job-41.png", "job-42.png"]}')
        if url.endswith("/api/v1/images/i/job-42.png/metadata"):
            return _FakeResponse(
                '{"app_version": "6.9.0", "model": {"name": "sdxl"}, '
                '"width": 1024, "height": 768}'
            )
        if url.endswith("/api/v1/images/i/job-42.png/urls"):
            return _FakeResponse(
                '{"thumbnail_url": "api/v1/images/i/job-42.png/thumbnail", '
                '"image_url": "api/v1/images/i/job-42.png/full"}'
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(
        DataStore(),
        base_url="http://bandit:9090",
        api_token="token-123",
    )
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.9.0"
    assert payload["invokeai.queue.pending_count"] == 4.0
    assert payload["invokeai.queue.in_progress_count"] == 2.0
    assert payload["invokeai.queue.failed_count"] == 1.0
    assert payload["invokeai.last_job.id"] == "42"
    assert payload["invokeai.last_job.status"] == "in_progress"
    assert payload["invokeai.last_job.model"] == "sdxl"
    assert payload["invokeai.last_job.dimensions"] == "1024 x 768"
    assert payload["invokeai.last_job.width"] == 1024.0
    assert payload["invokeai.last_job.height"] == 768.0
    assert payload["invokeai.last_job.completed_at"] == "2026-03-31T11:22:33Z"
    assert payload["invokeai.models.cache_used_mb"] == 6144.0
    assert payload["invokeai.models.cache_capacity_mb"] == 12288.0
    assert payload["invokeai.models.cache_percent"] == 50.0
    assert payload["invokeai.system.vram_percent"] == 50.0
    assert payload["invokeai.models.loaded_count"] == 2.0
    assert payload["invokeai.latest_image.name"] == "job-42.png"
    assert payload["invokeai.latest_image.thumbnail_url"] == (
        "http://bandit:9090/api/v1/images/i/job-42.png/thumbnail"
    )


async def test_invokeai_getter_idle_queue(monkeypatch) -> None:
    """Idle queue should still return stable defaults for last-job metadata."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse(
                '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}, '
                '"processor": {"is_started": true, "is_processing": false}}'
            )
        if url.endswith("/api/v1/queue/default/current"):
            return _FakeResponse("null")
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse('{"cache_size": 1073741824, "loaded_model_sizes": {}}')
        if url.endswith("/api/v1/images/names"):
            return _FakeResponse('{"image_names": []}')
        if url.endswith("/openapi.json"):
            return _FakeResponse('{"info": {"version": "6.9.0"}}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.9.0"
    assert payload["invokeai.queue.pending_count"] == 0.0
    assert payload["invokeai.queue.in_progress_count"] == 0.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.last_job.id"] == ""
    assert payload["invokeai.last_job.status"] == "idle"
    assert payload["invokeai.last_job.model"] == ""
    assert payload["invokeai.last_job.dimensions"] == ""
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
        body_map = {
            "/api/v1/queue/default/status": (
                '{"queue": {"pending": 1}, "processor": {"is_started": true}}'
            ),
            "/api/v1/queue/default/current": "null",
            "/api/v2/models/stats": (
                '{"cache_size": 25769803776, '
                '"loaded_model_sizes": {"flux": 1048576}}'
            ),
            "/api/v1/images/names": '{"image_names": ["job-1.png"]}',
            "/api/v1/images/i/job-1.png/metadata": (
                '{"model": {"name": "flux"}, "width": 512, "height": 768}'
            ),
            "/api/v1/images/i/job-1.png/urls": (
                '{"thumbnail_url": "api/v1/images/i/job-1.png/thumbnail"}'
            ),
            "/openapi.json": '{"info": {"version": "6.12.0.post1"}}',
        }

        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.12.0.post1"
    assert payload["invokeai.queue.pending_count"] == 1.0
    assert payload["invokeai.queue.in_progress_count"] == 0.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.last_job.id"] == "job-1.png"
    assert payload["invokeai.last_job.status"] == "completed"
    assert payload["invokeai.last_job.model"] == "flux"
    assert payload["invokeai.last_job.dimensions"] == "512 x 768"
    assert payload["invokeai.last_job.width"] == 512.0
    assert payload["invokeai.last_job.height"] == 768.0
    assert payload["invokeai.system.vram_used_mb"] == 1.0
    assert payload["invokeai.system.vram_total_mb"] == 24576.0
    assert payload["invokeai.models.loaded_count"] == 1.0


async def test_invokeai_getter_falls_back_from_html_endpoints(monkeypatch) -> None:
    """Getter should skip HTML fallback pages and use newer InvokeAI endpoints."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        body_map = {
            "/api/v1/queue/default/status": (
                '{"queue": {"pending": 3, "in_progress": 1, "failed": 2}, '
                '"processor": {"is_started": true, "is_processing": true}}'
            ),
            "/api/v1/queue/default/current": "<!DOCTYPE html><html><body>app</body></html>",
            "/api/v2/models/stats": (
                '{"loaded_model_sizes": {"a": 1000, "b": 2000, "c": 3000}, '
                '"cache_size": 21474836480}'
            ),
            "/api/v1/images/names": '{"image_names": ["abc.png"]}',
            "/api/v1/images/i/abc.png/metadata": '{"app_version": "6.12.0.post1"}',
            "/api/v1/images/i/abc.png/urls": (
                '{"thumbnail_url": "api/v1/images/i/abc.png/thumbnail"}'
            ),
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
    assert payload["invokeai.last_job.id"] == "abc.png"
    assert payload["invokeai.last_job.status"] == "in_progress"
    assert payload["invokeai.models.loaded_count"] == 3.0
    assert payload["invokeai.system.vram_used_mb"] == 6000.0
    assert payload["invokeai.system.vram_total_mb"] == 20480.0


async def test_invokeai_getter_tolerates_optional_timeout(monkeypatch) -> None:
    """Timeouts on optional enrichment endpoints should not fail the getter."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse('{"queue": {"pending": 2, "in_progress": 1, "failed": 0}}')
        if url.endswith("/api/v1/queue/default/current"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v2/models/stats"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v1/system/stats"):
            return _FakeResponse('{"system": {"vram_used_mb": 2048, "vram_total_mb": 4096}}')
        if url.endswith("/api/v1/images/names"):
            return _FakeResponse('{"image_names": ["job-7.png"]}')
        if url.endswith("/api/v1/images/i/job-7.png/metadata"):
            return _FakeResponse('{"app_version": "6.12.0.post1"}')
        if url.endswith("/api/v1/images/i/job-7.png/urls"):
            return _FakeResponse('{}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.12.0.post1"
    assert payload["invokeai.queue.pending_count"] == 2.0
    assert payload["invokeai.queue.in_progress_count"] == 1.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.last_job.id"] == "job-7.png"
    assert payload["invokeai.models.loaded_count"] == 0.0
    assert payload["invokeai.system.vram_used_mb"] == 2048.0


async def test_invokeai_getter_tolerates_queue_status_failure(monkeypatch) -> None:
    """Queue-status endpoint failure should still emit non-queue InvokeAI data."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/default/status"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v1/queue/status"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v1/queue/default/current"):
            return _FakeResponse("null")
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse('{"loaded_model_sizes": {"a": 1048576, "b": 2097152}}')
        if url.endswith("/api/v1/images/names"):
            return _FakeResponse('{"image_names": ["recent.png"]}')
        if url.endswith("/api/v1/images/i/recent.png/metadata"):
            return _FakeResponse('{"app_version": "6.12.0.post1", "model": {"name": "flux"}}')
        if url.endswith("/api/v1/images/i/recent.png/urls"):
            return _FakeResponse('{"thumbnail_url": "api/v1/images/i/recent.png/thumbnail"}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.12.0.post1"
    assert payload["invokeai.queue.pending_count"] == 0.0
    assert payload["invokeai.queue.in_progress_count"] == 0.0
    assert payload["invokeai.queue.failed_count"] == 0.0
    assert payload["invokeai.models.loaded_count"] == 2.0
    assert payload["invokeai.last_job.model"] == "flux"


async def test_invokeai_getter_uses_openapi_version_when_images_absent(monkeypatch) -> None:
    """OpenAPI version should backfill app version when image metadata is unavailable."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/queue/default/status"):
            return _FakeResponse('{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}')
        if url.endswith("/api/v1/queue/default/current"):
            return _FakeResponse("null")
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse('{"cache_size": 1073741824, "loaded_model_sizes": {}}')
        if url.endswith("/api/v1/images/names"):
            return _FakeResponse('{"image_names": []}')
        if url.endswith("/openapi.json"):
            return _FakeResponse('{"info": {"version": "6.10.1"}}')
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.10.1"
