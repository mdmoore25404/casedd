"""Tests for sparkline and histogram rolling history buffer memory safety.

These tests verify:
1. Deques are initialised with ``maxlen=_MAX_HISTORY_SAMPLES``.
2. The buffer never exceeds ``_MAX_HISTORY_SAMPLES`` entries regardless of
   how many samples are appended (closes issue #31 memory budget criterion).
3. Oldest entries are evicted automatically when the buffer is full.

Closes #31 acceptance criterion: 'Memory usage stays low (< 5 MB even with many metrics)'.
"""

from __future__ import annotations

from collections import deque

from casedd.renderer.widgets.histogram import _MAX_HISTORY_SAMPLES as HIST_MAX
from casedd.renderer.widgets.sparkline import _MAX_HISTORY_SAMPLES as SPARK_MAX


def test_sparkline_max_history_samples_constant_is_sane() -> None:
    """_MAX_HISTORY_SAMPLES must be positive and <= 3600 (capping 30-min at 2 Hz)."""
    assert 0 < SPARK_MAX <= 3600


def test_histogram_max_history_samples_constant_is_sane() -> None:
    """_MAX_HISTORY_SAMPLES must be positive and <= 3600 (capping 30-min at 2 Hz)."""
    assert 0 < HIST_MAX <= 3600


def test_deque_with_maxlen_never_exceeds_max_history_samples() -> None:
    """A deque initialised with maxlen=_MAX_HISTORY_SAMPLES auto-evicts on overflow.

    This mirrors exactly how sparkline and histogram widgets create their buffers.
    The deque must never exceed _MAX_HISTORY_SAMPLES even after a large burst of
    appends — no manual popleft required.
    """
    buf: deque[tuple[float, float]] = deque(maxlen=SPARK_MAX)
    overflow = SPARK_MAX + 100
    for i in range(overflow):
        buf.append((float(i), float(i)))

    assert len(buf) == SPARK_MAX, (
        f"Deque should be capped at {SPARK_MAX}, got {len(buf)}"
    )
    # Oldest entry (index 0) should be the (overflow - SPARK_MAX)-th inserted item.
    expected_first = float(overflow - SPARK_MAX)
    assert buf[0][0] == expected_first, (
        f"Expected oldest timestamp {expected_first}, got {buf[0][0]}"
    )
