"""InvokeAI API getter.

Polls InvokeAI HTTP endpoints and publishes flattened ``invokeai.*`` keys for
AI workstation dashboards.

Store keys written:
    - ``invokeai.version``
    - ``invokeai.queue.pending_count``
    - ``invokeai.queue.in_progress_count``
    - ``invokeai.queue.failed_count``
    - ``invokeai.last_job.id``
    - ``invokeai.last_job.status``
    - ``invokeai.last_job.model``
    - ``invokeai.last_job.dimensions``
    - ``invokeai.last_job.width``
    - ``invokeai.last_job.height``
    - ``invokeai.last_job.completed_at``
    - ``invokeai.system.vram_used_mb``
    - ``invokeai.system.vram_total_mb``
    - ``invokeai.system.vram_percent``
    - ``invokeai.models.cache_used_mb``
    - ``invokeai.models.cache_capacity_mb``
    - ``invokeai.models.cache_percent``
    - ``invokeai.models.loaded_count``
    - ``invokeai.latest_image.name``
    - ``invokeai.latest_image.thumbnail_url``
    - ``invokeai.latest_image.full_url``
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_QUEUE_STATUS_PATHS: tuple[str, ...] = (
    "/api/v1/queue/default/status",
    "/api/v1/queue/status",
)

_QUEUE_CURRENT_PATHS: tuple[str, ...] = (
    "/api/v1/queue/default/current",
)

_MODELS_PATHS: tuple[str, ...] = (
    "/api/v2/models/stats",
    "/api/v1/system/stats",
    "/api/v1/models",
)

_IMAGES_NAMES_PATH = "/api/v1/images/names"
_OPENAPI_PATH = "/openapi.json"

_MODEL_STICKY_KEYS: tuple[str, ...] = (
    "invokeai.system.vram_used_mb",
    "invokeai.system.vram_total_mb",
    "invokeai.system.vram_percent",
    "invokeai.models.cache_used_mb",
    "invokeai.models.cache_capacity_mb",
    "invokeai.models.cache_percent",
    "invokeai.models.loaded_count",
)

_LATEST_IMAGE_STICKY_KEYS: tuple[str, ...] = (
    "invokeai.last_job.id",
    "invokeai.last_job.status",
    "invokeai.last_job.model",
    "invokeai.last_job.dimensions",
    "invokeai.last_job.width",
    "invokeai.last_job.height",
    "invokeai.last_job.completed_at",
    "invokeai.latest_image.name",
    "invokeai.latest_image.thumbnail_url",
    "invokeai.latest_image.full_url",
)


class InvokeAIGetter(BaseGetter):
    """Getter for InvokeAI queue and runtime signals.

    Args:
        store: Shared data store.
        base_url: InvokeAI API base URL.
        api_token: Optional API token for bearer auth.
        interval: Poll interval in seconds.
        timeout: HTTP timeout in seconds.
        verify_tls: Verify TLS certificates for HTTPS endpoints.
    """

    def __init__(  # noqa: PLR0913 -- explicit config wiring is clearer
        self,
        store: DataStore,
        base_url: str = "http://localhost:9090",
        api_token: str | None = None,
        interval: float = 5.0,
        timeout: float = 4.0,
        verify_tls: bool = True,
    ) -> None:
        """Initialize InvokeAI getter settings."""
        super().__init__(store, interval)
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token.strip() if isinstance(api_token, str) else ""
        self._timeout = timeout
        self._ssl_context: ssl.SSLContext | None = None
        self._auth_error_logged = False
        self._sticky_values: dict[str, StoreValue] = {}
        if self._base_url.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one InvokeAI sample and normalize to flattened keys."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking InvokeAI poll implementation."""
        queue_payload = self._fetch_queue_payload()
        if queue_payload is None:
            return _placeholder_sample()
        if not queue_payload:
            _log.debug("InvokeAI queue status unavailable; emitting partial sample")

        current_payload = self._request_json_or_null_optional_first(_QUEUE_CURRENT_PATHS)
        models_payload = self._request_json_optional_first(_MODELS_PATHS)
        (
            latest_image_name,
            latest_image_metadata,
            latest_image_urls,
        ) = self._fetch_latest_image_data()

        sample = _placeholder_sample()
        sample.update(self._build_queue_fields(queue_payload))
        sample.update(
            self._build_activity_fields(
                queue_payload,
                current_payload,
                latest_image_name,
                latest_image_metadata,
            )
        )
        sample.update(self._build_cache_fields(models_payload))
        sample.update(self._build_latest_image_fields(latest_image_name, latest_image_urls))
        sample["invokeai.version"] = self._resolve_version(latest_image_metadata)

        version = str(sample["invokeai.version"])
        if version:
            self._sticky_values["invokeai.version"] = version
        elif "invokeai.version" in self._sticky_values:
            sample["invokeai.version"] = self._sticky_values["invokeai.version"]

        self._apply_sticky_group(
            sample,
            _MODEL_STICKY_KEYS,
            fresh_available=bool(models_payload),
        )

        latest_image_available = bool(
            latest_image_name or latest_image_metadata or latest_image_urls
        )
        if current_payload is None:
            self._apply_sticky_group(
                sample,
                _LATEST_IMAGE_STICKY_KEYS,
                fresh_available=latest_image_available,
            )

        return sample

    def _fetch_queue_payload(self) -> dict[str, object] | None:
        """Fetch queue status while keeping auth failures explicit."""
        try:
            return self._request_json_optional_first(_QUEUE_STATUS_PATHS)
        except RuntimeError as exc:
            if "auth failed" not in str(exc).lower():
                raise
            if not self._auth_error_logged:
                _log.error(
                    "InvokeAI auth failed. Check CASEDD_INVOKEAI_API_TOKEN and base URL: %s",
                    self._base_url,
                )
                self._auth_error_logged = True
            return None

    def _fetch_latest_image_data(
        self,
    ) -> tuple[str, dict[str, object], dict[str, object]]:
        """Fetch latest image name plus optional metadata and URL payloads."""
        image_names_payload = self._request_json_optional(_IMAGES_NAMES_PATH)
        latest_image_name = _extract_latest_image_name(image_names_payload)
        if not latest_image_name:
            return "", {}, {}

        encoded_image_name = quote(latest_image_name, safe="")
        latest_image_metadata = self._request_json_optional(
            f"/api/v1/images/i/{encoded_image_name}/metadata"
        )
        latest_image_urls = self._request_json_optional(
            f"/api/v1/images/i/{encoded_image_name}/urls"
        )
        return latest_image_name, latest_image_metadata, latest_image_urls

    def _build_queue_fields(self, queue_payload: dict[str, object]) -> dict[str, StoreValue]:
        """Normalize queue counters from the status payload."""
        pending_count = _first_number(
            queue_payload,
            [("pending_count",), ("pending",), ("counts", "pending"), ("queue", "pending")],
        )
        in_progress_count = _first_number(
            queue_payload,
            [
                ("in_progress_count",),
                ("in_progress",),
                ("running",),
                ("active",),
                ("counts", "in_progress"),
                ("queue", "in_progress"),
            ],
        )
        failed_count = _first_number(
            queue_payload,
            [("failed_count",), ("failed",), ("counts", "failed"), ("queue", "failed")],
        )
        return {
            "invokeai.queue.pending_count": float(pending_count),
            "invokeai.queue.in_progress_count": float(in_progress_count),
            "invokeai.queue.failed_count": float(failed_count),
        }

    def _build_activity_fields(
        self,
        queue_payload: dict[str, object],
        current_payload: dict[str, object] | None,
        latest_image_name: str,
        latest_image_metadata: dict[str, object],
    ) -> dict[str, StoreValue]:
        """Build current-or-latest job metadata for display widgets."""
        active_job = current_payload if current_payload is not None else {}
        model_obj = _first_object(
            active_job,
            [
                ("model",),
                ("model_info",),
                ("field_values", "model"),
                ("field_values", "model_info"),
            ],
        )
        dimensions_obj = _first_object(
            active_job,
            [("dimensions",), ("image",), ("output",), ("field_values",)],
        )

        last_job_id = _first_text(active_job, [("id",), ("item_id",), ("queue_id",)])
        if not last_job_id:
            last_job_id = latest_image_name

        last_job_model = _first_text(
            active_job,
            [
                ("model",),
                ("model_name",),
                ("model_id",),
                ("field_values", "model"),
                ("field_values", "model_name"),
                ("field_values", "model_id"),
            ],
        )
        if not last_job_model and model_obj is not None:
            last_job_model = _first_text(
                model_obj,
                [("name",), ("identifier",), ("model_name",)],
            )
        if not last_job_model:
            last_job_model = _first_text(latest_image_metadata, [("model", "name"), ("model",)])

        width = _first_number(active_job, [("width",), ("field_values", "width")])
        if width <= 0.0 and dimensions_obj is not None:
            width = _first_number(dimensions_obj, [("width",), ("w",)])
        if width <= 0.0:
            width = _first_number(latest_image_metadata, [("width",)])

        height = _first_number(active_job, [("height",), ("field_values", "height")])
        if height <= 0.0 and dimensions_obj is not None:
            height = _first_number(dimensions_obj, [("height",), ("h",)])
        if height <= 0.0:
            height = _first_number(latest_image_metadata, [("height",)])

        completed_at = _first_text(
            active_job,
            [
                ("completed_at",),
                ("finished_at",),
                ("started_at",),
                ("updated_at",),
                ("created_at",),
            ],
        )
        if not completed_at:
            completed_at = _first_text(latest_image_metadata, [("created_at",), ("updated_at",)])

        return {
            "invokeai.last_job.id": last_job_id,
            "invokeai.last_job.status": _derive_activity_status(
                active_job,
                queue_payload,
                has_latest_image=bool(latest_image_name),
            ),
            "invokeai.last_job.model": last_job_model,
            "invokeai.last_job.dimensions": _format_dimensions(width, height),
            "invokeai.last_job.width": float(width),
            "invokeai.last_job.height": float(height),
            "invokeai.last_job.completed_at": completed_at,
        }

    def _build_cache_fields(self, models_payload: dict[str, object]) -> dict[str, StoreValue]:
        """Build model-cache usage fields from the available stats payload."""
        cache_used_mb = _extract_cache_used_mb(models_payload)
        cache_capacity_mb = _extract_cache_capacity_mb(models_payload)
        cache_percent = 0.0
        if cache_capacity_mb > 0.0:
            cache_percent = min(100.0, (cache_used_mb / cache_capacity_mb) * 100.0)

        return {
            "invokeai.system.vram_used_mb": float(cache_used_mb),
            "invokeai.system.vram_total_mb": float(cache_capacity_mb),
            "invokeai.system.vram_percent": float(cache_percent),
            "invokeai.models.cache_used_mb": float(cache_used_mb),
            "invokeai.models.cache_capacity_mb": float(cache_capacity_mb),
            "invokeai.models.cache_percent": float(cache_percent),
            "invokeai.models.loaded_count": float(_extract_loaded_count(models_payload)),
        }

    def _build_latest_image_fields(
        self,
        latest_image_name: str,
        latest_image_urls: dict[str, object],
    ) -> dict[str, StoreValue]:
        """Build latest-image preview fields from the URLs payload."""
        return {
            "invokeai.latest_image.name": latest_image_name,
            "invokeai.latest_image.thumbnail_url": self._absolute_url(
                _first_text(latest_image_urls, [("thumbnail_url",)])
            ),
            "invokeai.latest_image.full_url": self._absolute_url(
                _first_text(latest_image_urls, [("image_url",)])
            ),
        }

    def _resolve_version(self, latest_image_metadata: dict[str, object]) -> str:
        """Resolve the best available InvokeAI app version string."""
        version = _first_text(latest_image_metadata, [("app_version",)])
        if not version:
            sticky_value = self._sticky_values.get("invokeai.version", "")
            version = str(sticky_value) if isinstance(sticky_value, str) else ""
        if not version:
            openapi_payload = self._request_json_optional(_OPENAPI_PATH)
            version = _first_text(openapi_payload, [("info", "version")])
        return version

    def _apply_sticky_group(
        self,
        sample: dict[str, StoreValue],
        keys: tuple[str, ...],
        *,
        fresh_available: bool,
    ) -> None:
        """Persist stable optional values across transient endpoint failures."""
        if fresh_available:
            for key in keys:
                self._sticky_values[key] = sample[key]
            return

        for key in keys:
            if key in self._sticky_values:
                sample[key] = self._sticky_values[key]

    def _absolute_url(self, path: str) -> str:
        """Return an absolute URL for an InvokeAI relative asset path."""
        if not path:
            return ""
        return urljoin(f"{self._base_url}/", path)

    def _request_json_or_null(self, path: str) -> dict[str, object] | None:
        """GET one endpoint and accept either a JSON object or explicit null."""
        decoded = self._request_json_value(path)
        if decoded is None:
            return None
        if not isinstance(decoded, dict):
            raise RuntimeError("InvokeAI response payload is not a JSON object")
        return decoded

    def _request_json_or_null_optional_first(
        self,
        paths: tuple[str, ...],
    ) -> dict[str, object] | None:
        """Best-effort request for a nullable JSON object endpoint."""
        last_error: RuntimeError | None = None
        for path in paths:
            try:
                return self._request_json_or_null(path)
            except RuntimeError as exc:
                if "auth failed" in str(exc).lower():
                    raise
                last_error = exc
                _log.debug("InvokeAI endpoint unavailable: %s (%s)", path, exc)

        if last_error is not None:
            _log.debug("InvokeAI optional nullable endpoint unavailable: %s", last_error)
        return None

    def _request_json(self, path: str) -> dict[str, object]:
        """GET one InvokeAI endpoint and parse a JSON object payload."""
        decoded = self._request_json_value(path)
        if not isinstance(decoded, dict):
            raise RuntimeError("InvokeAI response payload is not a JSON object")
        return decoded

    def _request_json_value(self, path: str) -> object | None:
        """GET one InvokeAI endpoint and parse any JSON payload."""
        url = f"{self._base_url}{path}"
        headers = {"Accept": "application/json"}
        if self._api_token:
            headers["Authorization"] = f"Bearer {self._api_token}"

        req = Request(url, headers=headers, method="GET")  # noqa: S310
        try:
            with urlopen(  # noqa: S310
                req,
                timeout=self._timeout,
                context=self._ssl_context,
            ) as resp:
                body = resp.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in {401, 403}:
                msg = "InvokeAI auth failed (check token credentials)"
                raise RuntimeError(msg) from exc
            raise RuntimeError(f"InvokeAI request failed with HTTP {exc.code}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"InvokeAI request timed out: {exc}") from exc
        except URLError as exc:
            raise RuntimeError(f"InvokeAI transport error: {exc}") from exc

        try:
            decoded = cast("object | None", json.loads(body))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"InvokeAI JSON parse error: {exc}") from exc
        return decoded

    def _request_json_optional(self, path: str) -> dict[str, object]:
        """Best-effort request for optional enrichment endpoints."""
        try:
            return self._request_json(path)
        except RuntimeError as exc:
            _log.debug("InvokeAI optional endpoint unavailable: %s (%s)", path, exc)
            return {}

    def _request_json_first(self, paths: tuple[str, ...]) -> dict[str, object]:
        """Return first successful JSON object from candidate endpoint paths."""
        last_error: RuntimeError | None = None
        for path in paths:
            try:
                return self._request_json(path)
            except RuntimeError as exc:
                if "auth failed" in str(exc).lower():
                    raise
                last_error = exc
                _log.debug("InvokeAI endpoint unavailable: %s (%s)", path, exc)

        if last_error is None:
            msg = "InvokeAI request failed for all endpoint candidates"
            raise RuntimeError(msg)
        raise last_error

    def _request_json_optional_first(self, paths: tuple[str, ...]) -> dict[str, object]:
        """Best-effort JSON request across multiple candidate endpoint paths."""
        try:
            return self._request_json_first(paths)
        except RuntimeError as exc:
            if "auth failed" in str(exc).lower():
                raise
            _log.debug("InvokeAI optional endpoint candidates unavailable: %s", exc)
            return {}


def _placeholder_sample() -> dict[str, StoreValue]:
    """Return a stable placeholder payload for auth-failure scenarios."""
    return {
        "invokeai.version": "—",
        "invokeai.queue.pending_count": 0.0,
        "invokeai.queue.in_progress_count": 0.0,
        "invokeai.queue.failed_count": 0.0,
        "invokeai.last_job.id": "—",
        "invokeai.last_job.status": "—",
        "invokeai.last_job.model": "—",
        "invokeai.last_job.dimensions": "—",
        "invokeai.last_job.width": 0.0,
        "invokeai.last_job.height": 0.0,
        "invokeai.last_job.completed_at": "—",
        "invokeai.system.vram_used_mb": 0.0,
        "invokeai.system.vram_total_mb": 0.0,
        "invokeai.system.vram_percent": 0.0,
        "invokeai.models.cache_used_mb": 0.0,
        "invokeai.models.cache_capacity_mb": 0.0,
        "invokeai.models.cache_percent": 0.0,
        "invokeai.models.loaded_count": 0.0,
        "invokeai.latest_image.name": "",
        "invokeai.latest_image.thumbnail_url": "",
        "invokeai.latest_image.full_url": "",
    }


def _first_object(
    payload: dict[str, object],
    paths: list[tuple[str, ...]],
) -> dict[str, object] | None:
    """Return first object found at one of ``paths``."""
    for path in paths:
        value = _value_at_path(payload, path)
        if isinstance(value, dict):
            return value
    return None


def _first_text(
    payload: dict[str, object],
    paths: list[tuple[str, ...]],
) -> str:
    """Return first non-empty text found at one of ``paths``."""
    for path in paths:
        value = _value_at_path(payload, path)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        if isinstance(value, int | float) and not isinstance(value, bool):
            return str(value)
    return ""


def _first_bool(
    payload: dict[str, object],
    paths: list[tuple[str, ...]],
) -> bool | None:
    """Return first boolean found at one of ``paths``."""
    for path in paths:
        value = _value_at_path(payload, path)
        if isinstance(value, bool):
            return value
    return None


def _first_number(
    payload: dict[str, object],
    paths: list[tuple[str, ...]],
) -> float:
    """Return first numeric value found at one of ``paths``."""
    for path in paths:
        value = _value_at_path(payload, path)
        if isinstance(value, bool):
            continue
        if isinstance(value, float | int):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                continue
    return 0.0


def _value_at_path(payload: dict[str, object], path: tuple[str, ...]) -> object | None:
    """Resolve a nested dictionary path safely."""
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extract_loaded_count(payload: dict[str, object]) -> float:
    """Extract loaded model count from flexible model endpoint payloads."""
    direct = _first_number(
        payload,
        [
            ("loaded_count",),
            ("models_loaded",),
            ("loaded", "count"),
        ],
    )
    if direct > 0.0:
        return direct

    for key in ("models", "loaded", "items", "results"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return float(len(rows))

    loaded_model_sizes = payload.get("loaded_model_sizes")
    if isinstance(loaded_model_sizes, dict):
        return float(len(loaded_model_sizes))

    return 0.0


def _extract_latest_image_name(payload: dict[str, object]) -> str:
    """Return the most recent image name from the image index payload."""
    rows = payload.get("image_names")
    if not isinstance(rows, list):
        return ""

    names = [row.strip() for row in rows if isinstance(row, str) and row.strip()]
    if not names:
        return ""
    return names[-1]


def _normalize_mebibytes(value: float) -> float:
    """Normalize either raw bytes or MiB-ish values into MiB."""
    if value >= 1024.0 * 1024.0:
        return value / (1024.0 * 1024.0)
    return value


def _extract_cache_used_mb(payload: dict[str, object]) -> float:
    """Extract current model-cache usage in MiB from the models payload."""
    loaded_model_sizes = payload.get("loaded_model_sizes")
    if isinstance(loaded_model_sizes, dict):
        total_bytes = 0.0
        for value in loaded_model_sizes.values():
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                total_bytes += float(value)
            elif isinstance(value, str):
                try:
                    total_bytes += float(value.strip())
                except ValueError:
                    continue
        if total_bytes > 0.0:
            return _normalize_mebibytes(total_bytes)

    return _normalize_mebibytes(
        _first_number(
            payload,
            [
                ("vram_used_mb",),
                ("gpu", "vram_used_mb"),
                ("system", "vram_used_mb"),
                ("high_watermark",),
            ],
        )
    )


def _extract_cache_capacity_mb(payload: dict[str, object]) -> float:
    """Extract total model-cache capacity in MiB from the models payload."""
    return _normalize_mebibytes(
        _first_number(
            payload,
            [
                ("cache_size",),
                ("vram_total_mb",),
                ("gpu", "vram_total_mb"),
                ("system", "vram_total_mb"),
            ],
        )
    )


def _format_dimensions(width: float, height: float) -> str:
    """Return a human-friendly dimensions string for non-zero sizes."""
    if width <= 0.0 or height <= 0.0:
        return ""
    return f"{int(width)} x {int(height)}"


def _derive_activity_status(
    current_payload: dict[str, object],
    queue_payload: dict[str, object],
    *,
    has_latest_image: bool,
) -> str:
    """Derive a user-facing activity state from current item and queue status."""
    status = _first_text(current_payload, [("status",), ("state",)])
    if status:
        return status

    in_progress_count = _first_number(
        queue_payload,
        [("in_progress",), ("queue", "in_progress"), ("counts", "in_progress")],
    )
    pending_count = _first_number(
        queue_payload,
        [("pending",), ("queue", "pending"), ("counts", "pending")],
    )
    processor = _first_object(queue_payload, [("processor",)]) or {}
    is_processing = _first_bool(processor, [("is_processing",)])
    is_paused = _first_bool(processor, [("is_paused",)])
    is_started = _first_bool(processor, [("is_started",)])

    if in_progress_count > 0.0 or is_processing is True:
        status = "in_progress"
    elif pending_count > 0.0 and not has_latest_image:
        status = "queued"
    elif is_paused is True:
        status = "paused"
    elif has_latest_image:
        status = "completed"
    elif is_started is True:
        status = "idle"
    else:
        status = ""

    return status
