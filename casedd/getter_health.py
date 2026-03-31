"""Getter health tracking registry for CASEDD.

Each :class:`~casedd.getters.base.BaseGetter` reports fetch outcomes here so
that the ``/api/health`` endpoint can surface per-getter status without
coupling the base class to the HTTP layer.

Public API:
    - :class:`GetterHealthRegistry` — singleton-style registry, pass to getters
"""

import threading
import time

_ERROR_LOG_THROTTLE_SEC: float = 60.0  # minimum seconds between repeated error logs


class GetterHealthRegistry:
    """Thread-safe registry tracking health of all active getters.

    Getters call :meth:`record_success` or :meth:`record_error` after each
    fetch attempt. The registry exposes a snapshot of all getter statuses for
    the ``/api/health`` endpoint.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._lock = threading.Lock()
        # name → {status, error_count, last_error_at, last_error_msg,
        #          last_success_at, consecutive_errors}
        self._entries: dict[str, dict[str, object]] = {}
        # Last wall-clock time we logged an error per getter, for throttling.
        self._last_log_at: dict[str, float] = {}

    def register(self, name: str) -> None:
        """Register a getter by name (call once at startup).

        Args:
            name: Getter class name or identifier.
        """
        with self._lock:
            if name not in self._entries:
                self._entries[name] = {
                    "status": "starting",
                    "error_count": 0,
                    "consecutive_errors": 0,
                    "last_error_at": None,
                    "last_error_msg": None,
                    "last_success_at": None,
                }

    def record_success(self, name: str) -> None:
        """Record a successful fetch for *name*.

        Args:
            name: Getter identifier (must have been :meth:`register`-ed).
        """
        with self._lock:
            entry = self._entries.setdefault(name, {})
            entry["status"] = "ok"
            entry["consecutive_errors"] = 0
            entry["last_success_at"] = time.time()

    def record_error(self, name: str, msg: str) -> bool:
        """Record a fetch error for *name*.

        Args:
            name: Getter identifier.
            msg: Short error description (exception str).

        Returns:
            ``True`` when the caller should log the error (rate-limited);
            ``False`` if a recent log was already emitted.
        """
        now = time.time()
        with self._lock:
            entry = self._entries.setdefault(name, {})
            entry["status"] = "error"
            prev_count = entry.get("error_count")
            count_now = (int(prev_count) if isinstance(prev_count, (int, float)) else 0) + 1
            entry["error_count"] = count_now
            prev_consec = entry.get("consecutive_errors")
            entry["consecutive_errors"] = (
                int(prev_consec) if isinstance(prev_consec, (int, float)) else 0
            ) + 1
            entry["last_error_at"] = now
            entry["last_error_msg"] = msg

            last = self._last_log_at.get(name, 0.0)
            should_log = (now - last) >= _ERROR_LOG_THROTTLE_SEC
            if should_log:
                self._last_log_at[name] = now
        return should_log

    def snapshot(self) -> list[dict[str, object]]:
        """Return a list of getter status dicts for all registered getters.

        Returns:
            List of dicts with keys: ``name``, ``status``, ``error_count``,
            ``consecutive_errors``, ``last_error_at``, ``last_error_msg``,
            ``last_success_at``.
        """
        with self._lock:
            return [
                {"name": name, **entry}
                for name, entry in sorted(self._entries.items())
            ]

    def any_ok(self) -> bool:
        """Return True if at least one getter has succeeded.

        Returns:
            ``True`` when any getter has ``status == 'ok'``.
        """
        with self._lock:
            return any(e.get("status") == "ok" for e in self._entries.values())

    def all_ok(self) -> bool:
        """Return True when every registered getter is currently healthy.

        Returns:
            ``True`` when all getters have ``status == 'ok'``.
        """
        with self._lock:
            return all(e.get("status") == "ok" for e in self._entries.values())
