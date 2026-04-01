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
    - ``invokeai.last_job.width``
    - ``invokeai.last_job.height``
    - ``invokeai.last_job.completed_at``
    - ``invokeai.system.vram_used_mb``
    - ``invokeai.system.vram_total_mb``
    - ``invokeai.models.loaded_count``
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_QUEUE_STATUS_PATHS: tuple[str, ...] = (
    "/api/v1/queue/default/status",
    "/api/v1/queue/status",
)

_QUEUE_ITEMS_PATHS: tuple[str, ...] = (
    "/api/v1/queue/items",
    "/api/v1/queue/default/list_all",
)

_MODELS_PATHS: tuple[str, ...] = (
    "/api/v1/models",
    "/api/v2/models/stats",
)

_SYSTEM_PATHS: tuple[str, ...] = (
    "/api/v1/system/stats",
    "/api/v2/models/stats",
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
        if self._base_url.startswith("https://") and not verify_tls:
            self._ssl_context = ssl._create_unverified_context()  # noqa: S323

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one InvokeAI sample and normalize to flattened keys."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking InvokeAI poll implementation."""
        try:
            queue_payload = self._request_json_optional_first(_QUEUE_STATUS_PATHS)
        except RuntimeError as exc:
            if "auth failed" in str(exc).lower():
                if not self._auth_error_logged:
                    _log.error(
                        "InvokeAI auth failed. Check CASEDD_INVOKEAI_API_TOKEN and base URL: %s",
                        self._base_url,
                    )
                    self._auth_error_logged = True
                return _placeholder_sample()
            raise

        if not queue_payload:
            _log.debug("InvokeAI queue status unavailable; emitting partial sample")

        jobs_payload = self._request_json_optional_first(_QUEUE_ITEMS_PATHS)
        version_payload = self._request_json_optional("/api/v1/app/version")
        system_payload = self._request_json_optional_first(_SYSTEM_PATHS)
        models_payload = self._request_json_optional_first(_MODELS_PATHS)

        pending_count = _first_number(
            queue_payload,
            [
                ("pending_count",),
                ("pending",),
                ("counts", "pending"),
                ("queue", "pending"),
            ],
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
            [
                ("failed_count",),
                ("failed",),
                ("counts", "failed"),
                ("queue", "failed"),
            ],
        )

        last_job = _select_last_job(jobs_payload)
        model_obj = _first_object(last_job, [("model",), ("model_info",)])
        dimensions_obj = _first_object(last_job, [("dimensions",), ("image",), ("output",)])

        last_job_id = _first_text(last_job, [("id",), ("item_id",), ("queue_id",)])
        last_job_status = _first_text(last_job, [("status",), ("state",)])
        last_job_model = _first_text(
            last_job,
            [
                ("model",),
                ("model_name",),
                ("model_id",),
            ],
        )
        if not last_job_model and model_obj is not None:
            last_job_model = _first_text(
                model_obj,
                [
                    ("name",),
                    ("identifier",),
                    ("model_name",),
                ],
            )

        width = _first_number(last_job, [("width",)])
        if width <= 0.0 and dimensions_obj is not None:
            width = _first_number(dimensions_obj, [("width",), ("w",)])

        height = _first_number(last_job, [("height",)])
        if height <= 0.0 and dimensions_obj is not None:
            height = _first_number(dimensions_obj, [("height",), ("h",)])

        completed_at = _first_text(
            last_job,
            [
                ("completed_at",),
                ("finished_at",),
                ("updated_at",),
                ("created_at",),
            ],
        )

        version = _first_text(version_payload, [("version",), ("app_version",)])

        vram_used_mb = _first_number(
            system_payload,
            [
                ("vram_used_mb",),
                ("gpu", "vram_used_mb"),
                ("system", "vram_used_mb"),
                ("high_watermark",),
            ],
        )
        vram_total_mb = _first_number(
            system_payload,
            [
                ("vram_total_mb",),
                ("gpu", "vram_total_mb"),
                ("system", "vram_total_mb"),
                ("cache_size",),
            ],
        )
        if vram_used_mb > 1024.0 * 1024.0:
            vram_used_mb /= 1024.0 * 1024.0
        if vram_total_mb > 1024.0 * 1024.0:
            vram_total_mb /= 1024.0 * 1024.0

        loaded_count = _extract_loaded_count(models_payload)

        return {
            "invokeai.version": version,
            "invokeai.queue.pending_count": float(pending_count),
            "invokeai.queue.in_progress_count": float(in_progress_count),
            "invokeai.queue.failed_count": float(failed_count),
            "invokeai.last_job.id": last_job_id,
            "invokeai.last_job.status": last_job_status,
            "invokeai.last_job.model": last_job_model,
            "invokeai.last_job.width": float(width),
            "invokeai.last_job.height": float(height),
            "invokeai.last_job.completed_at": completed_at,
            "invokeai.system.vram_used_mb": float(vram_used_mb),
            "invokeai.system.vram_total_mb": float(vram_total_mb),
            "invokeai.models.loaded_count": float(loaded_count),
        }

    def _request_json(self, path: str) -> dict[str, object]:
        """GET one InvokeAI endpoint and parse a JSON object payload."""
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
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"InvokeAI JSON parse error: {exc}") from exc

        if not isinstance(decoded, dict):
            raise RuntimeError("InvokeAI response payload is not a JSON object")
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
        "invokeai.last_job.width": 0.0,
        "invokeai.last_job.height": 0.0,
        "invokeai.last_job.completed_at": "—",
        "invokeai.system.vram_used_mb": 0.0,
        "invokeai.system.vram_total_mb": 0.0,
        "invokeai.models.loaded_count": 0.0,
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
    return ""


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


def _jobs_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    """Normalize queue-item payload to a list of job objects."""
    for key in ("items", "results", "jobs", "queue"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _select_last_job(payload: dict[str, object]) -> dict[str, object]:
    """Pick a representative last job from queue/items payload.

    Prefers completed/succeeded rows when present, then falls back to the
    first row for deterministic behavior.
    """
    jobs = _jobs_from_payload(payload)
    if not jobs:
        return {}

    for job in jobs:
        status = _first_text(job, [("status",), ("state",)]).lower()
        if status in {"completed", "succeeded", "success", "finished"}:
            return job

    return jobs[0]
