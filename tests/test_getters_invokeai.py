"""Tests for :mod:`casedd.getters.invokeai`."""

from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

from PIL import Image

from casedd.data_store import DataStore
from casedd.getters.invokeai import InvokeAIGetter


class _FakeResponse:
    """Minimal context-managed HTTP response for urlopen monkeypatching."""

    def __init__(self, body: str | bytes) -> None:
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _details_response_for_url(url: str) -> _FakeResponse | None:
    """Return a generic image-details response for optional detail endpoint calls."""
    marker = "/api/v1/images/i/"
    if marker not in url:
        return None
    if url.endswith(("/metadata", "/urls")):
        return None
    if url.endswith(("/workflow", "/thumbnail")):
        return None
    if url.endswith("/full"):
        return None

    image_name = url.split(marker, maxsplit=1)[1]
    if "/" in image_name:
        return None
    return _FakeResponse(
        "{"
        f'"image_name": "{image_name}", '
        '"width": 768, "height": 512'
        "}"
    )


async def test_invokeai_getter_active_queue(monkeypatch) -> None:
    """InvokeAI getter should flatten queue, current item, and preview values."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
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
        if url.endswith("/api/v1/images/?limit=1&order_dir=DESC&starred_first=false"):
            return _FakeResponse(
                '{"items": ['
                '{"image_name": "job-42.png", '
                '"thumbnail_url": "api/v1/images/i/job-42.png/thumbnail", '
                '"image_url": "api/v1/images/i/job-42.png/full"}'
                ']}'
            )
        if url.endswith("/api/v1/images/i/job-42.png/metadata"):
            return _FakeResponse(
                '{"app_version": "6.9.0", "model": {"name": "sdxl"}, '
                '"width": 1024, "height": 768}'
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

    body_map = {
        "/api/v1/queue/default/status": (
            '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}, '
            '"processor": {"is_started": true, "is_processing": false}}'
        ),
        "/api/v1/queue/default/current": "null",
        "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
        "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": '{"items": []}',
        "/api/v1/images/names": '{"image_names": []}',
        "/openapi.json": '{"info": {"version": "6.9.0"}}',
    }

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
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
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        body_map = {
            "/api/v1/queue/default/status": (
                '{"queue": {"pending": 1}, "processor": {"is_started": true}}'
            ),
            "/api/v1/queue/default/current": "null",
            "/api/v2/models/stats": (
                '{"cache_size": 25769803776, '
                '"loaded_model_sizes": {"flux": 1048576}}'
            ),
            "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
                '{"items": ['
                '{"image_name": "job-1.png", '
                '"thumbnail_url": "api/v1/images/i/job-1.png/thumbnail"}'
                ']}'
            ),
            "/api/v1/images/i/job-1.png/metadata": (
                '{"model": {"name": "flux"}, "width": 512, "height": 768}'
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
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
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
            "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
                '{"items": ['
                '{"image_name": "abc.png", '
                '"thumbnail_url": "api/v1/images/i/abc.png/thumbnail"}'
                ']}'
            ),
            "/api/v1/images/i/abc.png/metadata": '{"app_version": "6.12.0.post1"}',
            "/api/v1/images/i/abc.png/workflow": '{"graph": ""}',
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

    body_map: dict[str, str | Exception] = {
        "/api/v1/queue/default/status": '{"queue": {"pending": 2, "in_progress": 1, "failed": 0}}',
        "/api/v1/queue/default/current": TimeoutError("timed out"),
        "/api/v2/models/stats": TimeoutError("timed out"),
        "/api/v1/system/stats": '{"system": {"vram_used_mb": 2048, "vram_total_mb": 4096}}',
        "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
            '{"items": [{"image_name": "job-7.png"}]}'
        ),
        "/api/v1/images/i/job-7.png/urls": "{}",
        "/api/v1/images/i/job-7.png/workflow": '{"graph": ""}',
        "/api/v1/images/i/job-7.png/metadata": '{"app_version": "6.12.0.post1"}',
    }

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        for suffix, body in body_map.items():
            if not url.endswith(suffix):
                continue
            if isinstance(body, Exception):
                raise body
            return _FakeResponse(body)
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


async def test_invokeai_getter_prefers_newest_unstarred_image(monkeypatch) -> None:
    """Latest preview should come from the ordered image list, not the names feed."""

    body_map = {
        "/api/v1/queue/default/status": '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}',
        "/api/v1/queue/default/current": "null",
        "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
        "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
            '{"items": ['
            '{"image_name": "raccoon-taco.png", '
            '"thumbnail_url": "api/v1/images/i/raccoon-taco.png/thumbnail", '
            '"image_url": "api/v1/images/i/raccoon-taco.png/full", '
            '"created_at": "2026-03-31 23:59:59.000", '
            '"starred": false}'
            ']}'
        ),
        "/api/v1/images/i/raccoon-taco.png/metadata": (
            '{"app_version": "6.9.0", '
            '"positive_prompt": "raccoon eating a taco"}'
        ),
        "/api/v1/images/i/raccoon-taco.png/workflow": '{"graph": ""}',
    }

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore(), base_url="http://bandit:9090")
    payload = await getter.fetch()

    assert payload["invokeai.last_job.id"] == "raccoon-taco.png"
    assert payload["invokeai.latest_image.name"] == "raccoon-taco.png"
    assert payload["invokeai.latest_image.thumbnail_url"] == (
        "http://bandit:9090/api/v1/images/i/raccoon-taco.png/thumbnail"
    )
    assert payload["invokeai.version"] == "6.9.0"


async def test_invokeai_getter_accepts_null_latest_image_metadata(monkeypatch) -> None:
    """Null metadata for the newest image should not block preview selection."""

    body_map = {
        "/api/v1/queue/default/status": '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}',
        "/api/v1/queue/default/current": "null",
        "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
        "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
            '{"items": ['
            '{"image_name": "latest.png", '
            '"thumbnail_url": "api/v1/images/i/latest.png/thumbnail"}'
            ']}'
        ),
        "/api/v1/images/i/latest.png/metadata": "null",
        "/api/v1/images/i/latest.png/workflow": '{"graph": ""}',
        "/openapi.json": '{"info": {"version": "6.9.0"}}',
    }

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore(), base_url="http://bandit:9090")
    payload = await getter.fetch()

    assert payload["invokeai.latest_image.name"] == "latest.png"
    assert payload["invokeai.latest_image.thumbnail_url"] == (
        "http://bandit:9090/api/v1/images/i/latest.png/thumbnail"
    )
    assert payload["invokeai.version"] == "6.9.0"


async def test_invokeai_getter_inferrs_size_from_url_endpoint(monkeypatch) -> None:
    """Getter should infer dimensions from image bytes when list metadata is sparse."""

    buf = BytesIO()
    Image.new("RGB", (640, 832), "white").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    body_map = {
        "/api/v1/queue/default/status": '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}',
        "/api/v1/queue/default/current": "null",
        "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
        "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
            '{"items": [{"image_name": "discord-api.png"}]}'
        ),
        "/api/v1/images/i/discord-api.png/metadata": "null",
        "/api/v1/images/i/discord-api.png": '{"image_name": "discord-api.png"}',
        "/api/v1/images/i/discord-api.png/workflow": '{"graph": ""}',
        "/api/v1/images/i/discord-api.png/urls": (
            '{"image_url": "api/v1/images/i/discord-api.png/full", '
            '"thumbnail_url": "api/v1/images/i/discord-api.png/thumbnail"}'
        ),
        "/openapi.json": '{"info": {"version": "6.9.0"}}',
    }

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if url.endswith("/api/v1/images/i/discord-api.png/full"):
            return _FakeResponse(png_bytes)
        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore(), base_url="http://bandit:9090")
    payload = await getter.fetch()

    assert payload["invokeai.last_job.id"] == "discord-api.png"
    assert payload["invokeai.last_job.dimensions"] == "640 x 832"
    assert payload["invokeai.last_job.width"] == 640.0
    assert payload["invokeai.last_job.height"] == 832.0


async def test_invokeai_getter_tolerates_queue_status_failure(monkeypatch) -> None:
    """Queue-status endpoint failure should still emit non-queue InvokeAI data."""

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        if url.endswith("/api/v1/queue/default/status"):
            raise TimeoutError("timed out")
        if url.endswith("/api/v1/queue/status"):
            return _FakeResponse("<!DOCTYPE html><html><body>app</body></html>")
        if url.endswith("/api/v1/queue/default/current"):
            return _FakeResponse("null")
        if url.endswith("/api/v2/models/stats"):
            return _FakeResponse('{"loaded_model_sizes": {"a": 1048576, "b": 2097152}}')
        if url.endswith("/api/v1/images/?limit=1&order_dir=DESC&starred_first=false"):
            return _FakeResponse(
                '{"items": ['
                '{"image_name": "recent.png", '
                '"thumbnail_url": "api/v1/images/i/recent.png/thumbnail"}'
                ']}'
            )
        if url.endswith("/api/v1/images/i/recent.png/metadata"):
            return _FakeResponse('{"app_version": "6.12.0.post1", "model": {"name": "flux"}}')
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

    body_map = {
        "/api/v1/queue/default/status": '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}',
        "/api/v1/queue/default/current": "null",
        "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
        "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": '{"items": []}',
        "/api/v1/images/names": '{"image_names": []}',
        "/openapi.json": '{"info": {"version": "6.10.1"}}',
    }

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        detail_response = _details_response_for_url(url)
        if detail_response is not None:
            return detail_response
        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore())
    payload = await getter.fetch()

    assert payload["invokeai.version"] == "6.10.1"


async def test_invokeai_getter_inferrs_dimensions_from_image_bytes(monkeypatch) -> None:
    """Getter should infer dimensions from latest image when metadata lacks size."""
    image = Image.new("RGB", (640, 832), (22, 22, 22))
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    image_bytes = encoded.getvalue()

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        if "api-bot.png" not in url:
            detail_response = _details_response_for_url(url)
            if detail_response is not None:
                return detail_response
        body_map: dict[str, str | bytes] = {
            "/api/v1/queue/default/status": (
                '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}'
            ),
            "/api/v1/queue/default/current": "null",
            "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
            "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
                '{"items": ['
                '{"image_name": "api-bot.png", '
                '"image_url": "api/v1/images/i/api-bot.png/full"}'
                "]}"
            ),
            "/api/v1/images/i/api-bot.png/metadata": '{"model_name": "flux-dev"}',
            "/api/v1/images/i/api-bot.png": "{}",
            "/api/v1/images/i/api-bot.png/full": image_bytes,
            "/openapi.json": '{"info": {"version": "6.9.0"}}',
        }

        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore(), base_url="http://bandit:9090")
    payload = await getter.fetch()

    assert payload["invokeai.last_job.model"] == "flux-dev"
    assert payload["invokeai.last_job.width"] == 640.0
    assert payload["invokeai.last_job.height"] == 832.0
    assert payload["invokeai.last_job.dimensions"] == "640 x 832"


async def test_invokeai_getter_prefers_metadata_dimensions(monkeypatch) -> None:
    """Metadata dimensions should be used before image-byte fallback probing."""

    image = Image.new("RGB", (640, 832), (22, 22, 22))
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    image_bytes = encoded.getvalue()

    def _ok(req, timeout: float, context=None):
        url = str(req.full_url)
        body_map: dict[str, str | bytes] = {
            "/api/v1/queue/default/status": (
                '{"queue": {"pending": 0, "in_progress": 0, "failed": 0}}'
            ),
            "/api/v1/queue/default/current": "null",
            "/api/v2/models/stats": '{"cache_size": 1073741824, "loaded_model_sizes": {}}',
            "/api/v1/images/?limit=1&order_dir=DESC&starred_first=false": (
                '{"items": ['
                '{"image_name": "meta-first.png", '
                '"image_url": "api/v1/images/i/meta-first.png/full"}'
                "]}"
            ),
            "/api/v1/images/i/meta-first.png/metadata": (
                '{"model_name": "flux-dev", "width": 1024, "height": 576}'
            ),
            "/api/v1/images/i/meta-first.png/full": image_bytes,
            "/openapi.json": '{"info": {"version": "6.9.0"}}',
        }

        for suffix, body in body_map.items():
            if url.endswith(suffix):
                return _FakeResponse(body)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("casedd.getters.invokeai.urlopen", _ok)

    getter = InvokeAIGetter(DataStore(), base_url="http://bandit:9090")
    payload = await getter.fetch()

    assert payload["invokeai.last_job.model"] == "flux-dev"
    assert payload["invokeai.last_job.width"] == 1024.0
    assert payload["invokeai.last_job.height"] == 576.0
    assert payload["invokeai.last_job.dimensions"] == "1024 x 576"
