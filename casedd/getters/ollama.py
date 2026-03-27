"""Ollama API getter.

Polls the Ollama HTTP API (``/api/ps``) and publishes active-model telemetry
under the ``ollama.*`` namespace.

Store keys written:
    - ``ollama.active_count`` (float)
    - ``ollama.active_models`` (str)
    - ``ollama.active_compact`` (str)
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

_GB = 1_000_000_000
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
        names = ", ".join(_display_model_name(m) for m in models if _model_name(m))
        compact_lines = [
            (
                f"{_display_model_name(m)}\t{_model_size_gb(m):.1f}GB"
                f"\t{_processor_display(m)}\t{_model_ttl_compact(m)}"
            )
            for m in models
        ]
        compact = "\n".join(compact_lines) if compact_lines else "--"

        summary = f"{count} active"
        primary = models[0] if models else None
        primary_name = _model_name(primary) if primary is not None else ""
        primary_size_gb = _model_size_gb(primary) if primary is not None else 0.0
        primary_ttl = _model_ttl(primary) if primary is not None else "n/a"
        primary_gpu_pct = _processor_pct(primary, _GPU_RE) if primary is not None else 0.0
        primary_cpu_pct = _processor_pct(primary, _CPU_RE) if primary is not None else 0.0

        if primary_name and primary is not None:
            summary = f"{_display_model_name(primary)} ({_model_ttl_compact(primary)})"

        return {
            "ollama.active_count": float(count),
            "ollama.active_models": names,
            "ollama.active_compact": compact,
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


def _display_model_name(model: dict[str, object] | None) -> str:
    """Return human-friendly model name for dashboard display.

    Strips the ``:latest`` tag suffix when present to keep the UI compact.

    Args:
        model: One model entry from ``/api/ps`` response.

    Returns:
        Display name without ``:latest`` suffix.
    """
    name = _model_name(model)
    return name.removesuffix(":latest")


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


def _model_ttl_compact(model: dict[str, object]) -> str:
    """Return compact TTL string for one-line dashboard summaries.

    Args:
        model: One model entry from ``/api/ps`` response.

    Returns:
        Compact TTL like ``41m`` or ``2h 05m``.
    """
    raw = model.get("expires_at")
    if not isinstance(raw, str) or not raw:
        return "n/a"
    try:
        expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "n/a"

    now = datetime.now(UTC)
    total_sec = int((expires - now).total_seconds())
    if total_sec <= 0:
        return "expired"
    total_min = total_sec // 60
    hours, mins = divmod(total_min, 60)
    if hours > 0:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def _processor_display(model: dict[str, object]) -> str:
    """Return processor placement text matching ``ollama ps`` semantics.

    Args:
        model: One model entry from ``/api/ps`` response.

    Returns:
        Display string such as ``100% GPU``, ``100% CPU``, or
        ``48%/52% CPU/GPU``.
    """
    by_size = _processor_display_from_size(model)
    if by_size is not None:
        return by_size

    by_pct = _processor_display_from_pct(model)
    if by_pct is not None:
        return by_pct

    by_text = _processor_display_from_text(model)
    if by_text is not None:
        return by_text

    return "Unknown"


def _processor_display_from_size(model: dict[str, object]) -> str | None:
    """Infer processor placement from ``size`` and ``size_vram``.

    This mirrors Ollama CLI behavior in ``cmd/cmd.go``.
    """
    size = model.get("size")
    size_vram = model.get("size_vram")
    if not isinstance(size, int | float) or not isinstance(size_vram, int | float):
        return None

    total = float(size)
    vram = float(size_vram)
    if total <= 0.0 or vram > total:
        return "Unknown"
    if vram == 0.0:
        return "100% CPU"
    if vram == total:
        return "100% GPU"

    size_cpu = total - vram
    cpu_percent = round((size_cpu / total) * 100.0)
    gpu_percent = int(100 - cpu_percent)
    return f"{int(cpu_percent)}%/{gpu_percent}% CPU/GPU"


def _processor_display_from_pct(model: dict[str, object]) -> str | None:
    """Infer processor display from explicit CPU/GPU percentages."""
    gpu = _processor_pct(model, _GPU_RE)
    cpu = _processor_pct(model, _CPU_RE)
    if gpu > 0.0 and cpu > 0.0:
        cpu_pct = round(cpu)
        gpu_pct = round(gpu)
        return f"{cpu_pct}%/{gpu_pct}% CPU/GPU"
    if gpu > 0.0:
        return "100% GPU"
    if cpu > 0.0:
        return "100% CPU"
    return None


def _processor_display_from_text(model: dict[str, object]) -> str | None:
    """Infer processor placement from textual processor descriptor."""
    raw = model.get("processor")
    if not isinstance(raw, str):
        return None
    upper = raw.upper()
    has_gpu = "GPU" in upper
    has_cpu = "CPU" in upper
    if has_gpu and has_cpu:
        return raw.replace(" / ", "/").strip()
    if has_gpu:
        return "100% GPU"
    if has_cpu:
        return "100% CPU"
    return None


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
