"""Helpers for normalizing speedtest timestamp display fields.

This module keeps the ``speedtest.last_run`` family of values consistent
across local speedtest runs, pushed updates, and restored cache data.
"""

from __future__ import annotations

from datetime import UTC, datetime

from casedd.data_store import StoreValue

SPEEDTEST_KEY_PREFIX = "speedtest."
SPEEDTEST_LAST_RUN_KEY = "speedtest.last_run"
SPEEDTEST_LAST_RUN_DATE_KEY = "speedtest.last_run_date"
SPEEDTEST_LAST_RUN_TIME_KEY = "speedtest.last_run_time"
SPEEDTEST_LAST_RUN_DISPLAY_KEY = "speedtest.last_run_display"


def now_local_timestamp() -> str:
    """Return the current local timestamp in the canonical speedtest format.

    Returns:
        Local timestamp string formatted as ``YYYY-MM-DD HH:MM:SS``.
    """
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _split_last_run(value: str) -> tuple[str, str]:
    """Split a speedtest timestamp into display-friendly date and time strings.

    Args:
        value: Raw timestamp string.

    Returns:
        Tuple of ``(date_text, time_text)``.
    """
    stripped = value.strip()
    if not stripped:
        return ("--", "--")

    iso_text = stripped.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        normalized = stripped.replace("T", " ", 1)
        parts = normalized.split(maxsplit=1)
        if len(parts) == 2:
            return (parts[0], parts[1])
        return (stripped, "")

    display_dt = parsed.astimezone() if parsed.tzinfo is not None else parsed
    return (
        display_dt.strftime("%Y-%m-%d"),
        display_dt.strftime("%H:%M:%S"),
    )


def enrich_speedtest_timestamp_fields(payload: dict[str, StoreValue]) -> None:
    """Ensure canonical and split speedtest timestamp fields exist in ``payload``.

    Args:
        payload: Flat data-store update payload to mutate in-place.
    """
    if not any(key.startswith(SPEEDTEST_KEY_PREFIX) for key in payload):
        return

    raw_timestamp = payload.get(SPEEDTEST_LAST_RUN_KEY)
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        raw_timestamp = now_local_timestamp()
        payload[SPEEDTEST_LAST_RUN_KEY] = raw_timestamp

    date_text, time_text = _split_last_run(raw_timestamp)
    payload[SPEEDTEST_LAST_RUN_DATE_KEY] = date_text
    payload[SPEEDTEST_LAST_RUN_TIME_KEY] = time_text or "--"
    payload[SPEEDTEST_LAST_RUN_DISPLAY_KEY] = (
        f"{date_text}\n{time_text}" if time_text else date_text
    )
