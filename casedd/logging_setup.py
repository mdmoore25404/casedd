"""Logging setup for CASEDD.

Configures two handlers:
- **Console** (stderr): colored output, respects ``log_level``.
- **Rotating file** (``logs/casedd.log``): plain text, always at DEBUG level
  so the full trace is preserved on disk regardless of console verbosity.

Public API:
    - :func:`setup_logging` — must be called once at daemon startup.
"""

import logging
import logging.handlers
from pathlib import Path
import sys

# ANSI escape codes for color — only applied when stderr is a real terminal
_RESET = "\033[0m"
_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",     # cyan
    logging.INFO: "\033[32m",      # green
    logging.WARNING: "\033[33m",   # yellow
    logging.ERROR: "\033[31m",     # red
    logging.CRITICAL: "\033[35m",  # magenta
}


class _ColorFormatter(logging.Formatter):
    """Formatter that adds ANSI color to the level name when outputting to a TTY.

    Color is stripped automatically when stderr is not a terminal (e.g. when
    output is piped or redirected to a file).
    """

    _is_tty: bool = sys.stderr.isatty()
    _fmt: str = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    _datefmt: str = "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record, adding color when appropriate.

        Args:
            record: The log record to format.

        Returns:
            The formatted log string.
        """
        if self._is_tty:
            color = _LEVEL_COLORS.get(record.levelno, "")
            record.levelname = f"{color}{record.levelname}{_RESET}"
        formatter = logging.Formatter(fmt=self._fmt, datefmt=self._datefmt)
        return formatter.format(record)


def setup_logging(log_level: str, log_dir: Path) -> None:
    """Configure the root logger with console and rotating file handlers.

    Should be called exactly once, as early as possible in ``__main__``.

    Args:
        log_level: stdlib log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory where ``casedd.log`` will be written. Created if absent.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "casedd.log"

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Console handler — colored, respects configured log level
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(_ColorFormatter())

    # Rotating file handler -- always DEBUG, 5 MB x 5 files
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # root must be DEBUG so file handler sees everything
    root.addHandler(console_handler)
    root.addHandler(file_handler)
