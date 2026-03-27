"""Entry point for ``python -m casedd``.

Handles:
- PID file creation and cleanup (prevents duplicate daemon instances).
- Logging initialisation.
- Configuration loading.
- Starting the async :class:`~casedd.daemon.Daemon`.

Usage::

    python -m casedd

All settings are controlled via environment variables or ``casedd.yaml``.
See ``.env.example`` for the full variable reference.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import sys

from casedd.config import load_config
from casedd.daemon import Daemon
from casedd.logging_setup import setup_logging

_log = logging.getLogger(__name__)

# Default PID file location (override via CASEDD_PID_FILE)
_DEFAULT_PID_FILE = Path("run/casedd.pid")


def _acquire_pid_file(pid_path: Path) -> None:
    """Write the current process PID to ``pid_path``.

    Raises ``SystemExit`` if another instance is already running (PID file
    exists and the owning process is alive).

    Args:
        pid_path: Path where the PID file should be written.
    """
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            old_pid = 0

        if old_pid > 0:
            # Check whether the PID is still alive
            try:
                os.kill(old_pid, 0)  # signal 0 = existence check, no actual signal
                _log.critical(
                    "CASEDD is already running (PID %d). Aborting.", old_pid
                )
                sys.exit(1)
            except (ProcessLookupError, PermissionError):
                # Process gone — stale PID file; safe to overwrite
                _log.warning("Stale PID file at %s (PID %d) — overwriting.", pid_path, old_pid)

    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    _log.debug("PID file written: %s (%d)", pid_path, os.getpid())


def _release_pid_file(pid_path: Path) -> None:
    """Remove the PID file on clean exit.

    Args:
        pid_path: Path of the PID file to remove.
    """
    pid_path.unlink(missing_ok=True)
    _log.debug("PID file removed: %s", pid_path)


def main() -> None:
    """Configure, start, and run the CASEDD daemon.

    This is the canonical entry point called by ``python -m casedd`` and by
    the ``casedd`` console script defined in ``pyproject.toml``.
    """
    # --- Load configuration (needed for log level and dirs) ---
    cfg = load_config()

    # --- Initialise logging early so all subsequent messages are routed ---
    log_dir = Path(os.environ.get("CASEDD_LOG_DIR", "logs"))
    setup_logging(cfg.log_level, log_dir)

    _log.info("CASEDD starting up.")
    _log.debug("Active config: %s", cfg)

    # --- PID file ---
    pid_path = Path(os.environ.get("CASEDD_PID_FILE", str(_DEFAULT_PID_FILE)))
    _acquire_pid_file(pid_path)

    try:
        asyncio.run(Daemon(cfg).run())
    except KeyboardInterrupt:
        # asyncio.run() converts SIGINT to KeyboardInterrupt after the loop exits
        _log.info("Interrupted by user.")
    except Exception:
        _log.critical("Unhandled exception in daemon:", exc_info=True)
        sys.exit(1)
    finally:
        _release_pid_file(pid_path)
        _log.info("CASEDD exited cleanly.")


if __name__ == "__main__":
    main()
