"""Tests for bounded getter-task shutdown handling.

Regression coverage for a host-reboot delay: an unreachable Tuya smart-plug
device (whose socket connect used tinytuya's generous default timeout/retry
settings) kept a getter's ``asyncio.to_thread``-wrapped fetch running for up
to ~150s. Because a task awaiting a non-cancellable executor call only
resolves once that call returns, daemon shutdown — and, when run under
systemd, host reboot/poweroff — could be delayed for minutes.
``Daemon._await_getter_shutdown`` bounds how long shutdown waits on getter
tasks; the root cause (unbounded tinytuya timeouts) is fixed separately in
``casedd/getters/tuya.py`` — see ``tests/test_getters_tuya.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from casedd.config import Config
from casedd.daemon import Daemon


@pytest.mark.asyncio
async def test_await_getter_shutdown_returns_once_tasks_finish() -> None:
    """Waiting completes promptly once every cancelled task actually exits."""
    daemon = Daemon(Config())

    async def _quick() -> None:
        await asyncio.sleep(0)

    tasks = {"quick": asyncio.create_task(_quick())}

    await asyncio.wait_for(
        daemon._await_getter_shutdown(tasks, timeout_seconds=1.0),
        timeout=2.0,
    )

    assert tasks["quick"].done()


@pytest.mark.asyncio
async def test_await_getter_shutdown_does_not_block_on_stuck_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A task that ignores cancellation must not stall shutdown indefinitely.

    Simulates a getter whose fetch keeps running after cancellation is
    requested (as observed in production: an ``asyncio.to_thread``-wrapped
    call — a Tuya smart-plug socket connect with generous internal retries —
    kept its worker thread alive well past the point CASEDD asked it to
    stop). ``_await_getter_shutdown`` must return within its own bounded
    timeout rather than waiting forever, and it must log a warning
    identifying the stuck task count, so the rest of the shutdown sequence
    (and, under systemd, host reboot/poweroff) is not delayed.
    """
    daemon = Daemon(Config())
    stuck_timeout = 0.2
    release = asyncio.Event()

    async def _stuck() -> None:
        # Swallow the cancellation and keep running, mirroring a task whose
        # underlying blocking call cannot actually be interrupted.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(stuck_timeout + 5)
        await asyncio.sleep(stuck_timeout + 5)
        release.set()

    task = asyncio.create_task(_stuck())
    await asyncio.sleep(0)
    task.cancel()

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(
            daemon._await_getter_shutdown({"stuck": task}, timeout_seconds=stuck_timeout),
            timeout=stuck_timeout + 2.0,
        )

    assert not release.is_set()
    assert any("did not stop within" in record.message for record in caplog.records)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
