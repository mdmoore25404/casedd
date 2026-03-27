"""Ollama API getter.

Polls the Ollama HTTP API (``/api/ps``) and publishes active-model telemetry
under the ``ollama.*`` namespace.

Store keys written:
    - ``ollama.active_count`` (float)
    - ``ollama.active_models`` (str)
    - ``ollama.primary_model`` (str)
    - ``ollama.primary_size_gb`` (float)
    - ``ollama.primary_gpu_percent`` (float)
    - ``ollama.primary_cpu_percent`` (float)
    - ``ollama.primary_ttl`` (str)
    - ``ollama.summary`` (str)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_GB = 1024 ** 3
_GPU_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*GPU", flags=re.IGNORECASE)
_CPU_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*CPU", flags=re.IGNORECASE)


class OllamaGetter(BaseGetter):
    """Getter for active Ollama model runtime information.

    Args:
        store: Shared data store.
        base_url: Ollama API base URL.
        interval: Poll interval in seconds.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        store: DataStore,
        base_url: str = "http://localhost:11434",
        interval: float = 10.0,
        timeout: float = 3.0,
    ) -> None:
        """Initialise Ollama API getter.

        Args:
            store: Shared data store instance.
            base_url: Base URL of Ollama API.
            interval: Poll interval in seconds.
            timeout: HTTP timeout in seconds.
        """
        super().__init__(store, interval)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def fetch(self) -> dict[str, StoreValue]:
        """Fetch current active-model state from Ollama.

        Returns:
            ``ollama.*`` keys or empty dict when API is unavailable.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Perform one synchronous Ollama API poll.

        Returns:
            Mapping of ``ollama.*`` keys.
        """
        url = f"{self._base_url}/api/ps"
        req = Request(url, method="GET")  # noqa: S310 -- configurable local API endpoint
        try:
            with urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 -- configurable local API endpoint
                raw = resp.read().decode("utf-8")
        except URLError as exc:
            _log.debug("Ollama API unavailable (%s): %s", url, exc)
            return {}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log.warning("Failed to parse Ollama API response: %s", exc)
            return {}

        models_obj = payload.get("models")
        if not isinstance(models_obj, list):
            return {}

        models = [m for m in models_obj if isinstance(m, dict)]
        count = len(models)
        names = ", ".join(_model_name(m) for m in models if _model_name(m))

        summary = f"{count} active"
        primary = models[0] if models else None
        primary_name = _model_name(primary) if primary is not None else ""
        primary_size_gb = _model_size_gb(primary) if primary is not None else 0.0
        primary_ttl = _model_ttl(primary) if primary is not None else "n/a"
        primary_gpu_pct = _processor_pct(primary, _GPU_RE) if primary is not None else 0.0
        primary_cpu_pct = _processor_pct(primary, _CPU_RE) if primary is not None else 0.0

        if primary_name:
            summary = f"{primary_name} ({primary_ttl})"

        return {
            "ollama.active_count": float(count),
            "ollama.active_models": names,
            "ollama.primary_model": primary_name,
            "ollama.primary_size_gb": round(primary_size_gb, 2),
            "ollama.primary_gpu_percent": round(primary_gpu_pct, 1),
            "ollama.primary_cpu_percent": round(primary_cpu_pct, 1),
            "ollama.primary_ttl": primary_ttl,
            "ollama.summary": summary,
        }


def _model_name(model: dict[str, object] | None) -> str:
    """Return model name from Ollama model object.

    Args:
        model: One model entry from ``/api/ps`` response.

    Returns:
        Model name string, or empty string when absent.
    """
    if model is None:
        return ""
    raw = model.get("name") or model.get("model")
    return raw if isinstance(raw, str) else ""


def _model_size_gb(model: dict[str, object]) -> float:
    """Return model size in GB from Ollama model object."""
    raw = model.get("size")
    if isinstance(raw, int | float):
        return float(raw) / _GB
    return 0.0


def _model_ttl(model: dict[str, object]) -> str:
    """Return approximate remaining TTL string from expires_at field."""
    raw = model.get("expires_at")
    if not isinstance(raw, str) or not raw:
        return "n/a"
    try:
        expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw

    now = datetime.now(UTC)
    remaining = expires - now
    total_sec = int(remaining.total_seconds())
    if total_sec <= 0:
        return "expired"
    mins, sec = divmod(total_sec, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m {sec}s"


def _processor_pct(model: dict[str, object], pattern: re.Pattern[str]) -> float:
    """Extract CPU/GPU percentage from optional processor text field."""
    raw = model.get("processor")
    if not isinstance(raw, str):
        return 0.0
    match = pattern.search(raw)
    if match is None:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0
