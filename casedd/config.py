"""Configuration loading for CASEDD.

Loads settings from (in priority order, highest first):
1. Environment variables prefixed ``CASEDD_``
2. A YAML config file (default: ``casedd.yaml``, path set by ``CASEDD_CONFIG``)
3. Hard-coded defaults defined in :class:`Config`

The resulting :class:`Config` instance is a frozen Pydantic v2 model — safe to
pass around freely without mutation risk.

Public API:
    - :func:`load_config` — build and return the active :class:`Config`
    - :class:`Config` — the frozen config model
"""

import os
from pathlib import Path

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass
import yaml


@dataclass(config=ConfigDict(frozen=True))
class Config:
    """Daemon-wide configuration.

    Attributes:
        log_level: Logging verbosity (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        no_fb: Disable framebuffer output entirely (dev / no-hardware mode).
        fb_device: Path to the framebuffer device file.
        ws_port: WebSocket server port.
        http_port: HTTP viewer / API port.
        socket_path: Unix domain socket path for JSON data-write pushes.
        template: Active template name (no extension; relative to ``templates/``).
        refresh_rate: Render frequency in Hz.
        width: Canvas width in pixels.
        height: Canvas height in pixels.
        templates_dir: Directory containing ``.casedd`` template files.
        assets_dir: Directory containing static assets.
        disk_mount: Filesystem mount point to monitor for disk metrics.
        viewer_bg: Default browser viewer page background color.
        speedtest_interval: Interval between speed tests in seconds.
        speedtest_advertised_down_mbps: Advertised download speed in Mb/s.
        speedtest_advertised_up_mbps: Advertised upload speed in Mb/s.
        speedtest_reference_down_mbps: Optional effective downlink baseline in Mb/s.
        speedtest_reference_up_mbps: Optional effective uplink baseline in Mb/s.
        speedtest_marginal_ratio: Ratio under which speeds are considered marginal.
        speedtest_critical_ratio: Ratio under which speeds are considered critical.
        speedtest_binary: Speedtest CLI binary name or absolute path.
        speedtest_server_id: Optional Ookla server ID to force test target.
        ollama_api_base: Base URL for Ollama HTTP API.
        ollama_interval: Ollama polling interval in seconds.
        ollama_timeout: Ollama request timeout in seconds.
    """

    log_level: str = Field(default="INFO")
    no_fb: bool = Field(default=False)
    fb_device: Path = Field(default=Path("/dev/fb1"))
    ws_port: int = Field(default=8765)
    http_port: int = Field(default=8080)
    socket_path: Path = Field(default=Path("/run/casedd/casedd.sock"))
    template: str = Field(default="system_stats")
    refresh_rate: float = Field(default=2.0)
    width: int = Field(default=800)
    height: int = Field(default=480)
    templates_dir: Path = Field(default=Path("templates"))
    assets_dir: Path = Field(default=Path("assets"))
    disk_mount: str = Field(default="/")
    viewer_bg: str = Field(default="#0d0f12")
    speedtest_interval: float = Field(default=1800.0)
    speedtest_advertised_down_mbps: float = Field(default=2000.0)
    speedtest_advertised_up_mbps: float = Field(default=200.0)
    speedtest_reference_down_mbps: float | None = Field(default=None)
    speedtest_reference_up_mbps: float | None = Field(default=None)
    speedtest_marginal_ratio: float = Field(default=0.9)
    speedtest_critical_ratio: float = Field(default=0.7)
    speedtest_binary: str = Field(default="speedtest")
    speedtest_server_id: str | None = Field(default=None)
    ollama_api_base: str = Field(default="http://localhost:11434")
    ollama_interval: float = Field(default=10.0)
    ollama_timeout: float = Field(default=3.0)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """Ensure log level is a recognised stdlib logging level name.

        Args:
            v: The raw log level string.

        Returns:
            The uppercased log level string.

        Raises:
            ValueError: If the level is not in the accepted set.
        """
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            msg = f"Invalid log level '{v}'. Must be one of: {', '.join(sorted(valid))}"
            raise ValueError(msg)
        return upper

    @field_validator("refresh_rate")
    @classmethod
    def _validate_refresh_rate(cls, v: float) -> float:
        """Ensure refresh rate is positive and sensibly bounded.

        Args:
            v: Refresh rate in Hz.

        Returns:
            The validated refresh rate.

        Raises:
            ValueError: If out of the allowed range.
        """
        if not (0.1 <= v <= 60.0):
            msg = f"refresh_rate must be between 0.1 and 60.0 Hz, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("speedtest_interval")
    @classmethod
    def _validate_speedtest_interval(cls, v: float) -> float:
        """Ensure speedtest interval is positive and practical.

        Args:
            v: Speedtest polling interval in seconds.

        Returns:
            Validated interval.

        Raises:
            ValueError: If interval is outside accepted bounds.
        """
        if not (60.0 <= v <= 86400.0):
            msg = f"speedtest_interval must be between 60 and 86400 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("speedtest_advertised_down_mbps", "speedtest_advertised_up_mbps")
    @classmethod
    def _validate_advertised_speeds(cls, v: float) -> float:
        """Ensure advertised speed values are positive.

        Args:
            v: Advertised speed value in Mb/s.

        Returns:
            Validated advertised speed.

        Raises:
            ValueError: If the value is not positive.
        """
        if v <= 0.0:
            msg = f"Advertised speed values must be > 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("speedtest_reference_down_mbps", "speedtest_reference_up_mbps")
    @classmethod
    def _validate_reference_speeds(cls, v: float | None) -> float | None:
        """Ensure optional reference speed values are positive.

        Args:
            v: Optional reference speed in Mb/s.

        Returns:
            Validated optional value.

        Raises:
            ValueError: If the value is present but non-positive.
        """
        if v is not None and v <= 0.0:
            msg = f"Reference speed values must be > 0 when set, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("speedtest_marginal_ratio", "speedtest_critical_ratio")
    @classmethod
    def _validate_threshold_ratios(cls, v: float) -> float:
        """Ensure threshold ratios are sensible percentages.

        Args:
            v: Ratio value between 0 and 1.

        Returns:
            Validated ratio.

        Raises:
            ValueError: If ratio is outside (0, 1].
        """
        if not (0.0 < v <= 1.0):
            msg = f"Threshold ratios must be between 0 and 1, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("ollama_interval")
    @classmethod
    def _validate_ollama_interval(cls, v: float) -> float:
        """Ensure Ollama polling interval is positive and practical.

        Args:
            v: Poll interval in seconds.

        Returns:
            Validated interval.

        Raises:
            ValueError: If interval is outside accepted bounds.
        """
        if not (1.0 <= v <= 3600.0):
            msg = f"ollama_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("ollama_timeout")
    @classmethod
    def _validate_ollama_timeout(cls, v: float) -> float:
        """Ensure Ollama timeout is a positive value.

        Args:
            v: Timeout in seconds.

        Returns:
            Validated timeout.

        Raises:
            ValueError: If timeout is non-positive.
        """
        if v <= 0.0:
            msg = f"ollama_timeout must be > 0, got {v}"
            raise ValueError(msg)
        return v


def _read_yaml(path: Path) -> dict[str, object]:
    """Read a YAML file and return its top-level mapping.

    Args:
        path: Path to the YAML file.

    Returns:
        The parsed YAML content as a dict. Returns an empty dict if the file
        does not exist or cannot be parsed.
    """
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return raw if isinstance(raw, dict) else {}


def load_config() -> Config:
    """Build and return the active daemon configuration.

    Merges YAML file settings and environment variable overrides, with env
    vars taking highest priority.

    Returns:
        A frozen :class:`Config` instance with all settings resolved.
    """
    # Determine config file path from env (before we've built Config)
    config_path = Path(os.environ.get("CASEDD_CONFIG", "casedd.yaml"))
    yaml_data = _read_yaml(config_path)

    # Helper: env var overrides yaml, yaml overrides default
    def _get(env_key: str, yaml_key: str, default: object) -> object:
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val
        return yaml_data.get(yaml_key, default)

    def _get_optional_float(env_key: str, yaml_key: str) -> float | None:
        """Parse an optional float from env/yaml merged config."""
        raw = str(_get(env_key, yaml_key, "")).strip()
        if not raw:
            return None
        return float(raw)

    return Config(
        log_level=str(_get("CASEDD_LOG_LEVEL", "log_level", "INFO")),
        no_fb=str(_get("CASEDD_NO_FB", "no_fb", "0")) not in {"0", "false", "False", ""},
        fb_device=Path(str(_get("CASEDD_FB_DEVICE", "fb_device", "/dev/fb1"))),
        ws_port=int(str(_get("CASEDD_WS_PORT", "ws_port", 8765))),
        http_port=int(str(_get("CASEDD_HTTP_PORT", "http_port", 8080))),
        socket_path=Path(
            str(_get("CASEDD_SOCKET_PATH", "socket_path", "/run/casedd/casedd.sock"))
        ),
        template=str(_get("CASEDD_TEMPLATE", "template", "system_stats")),
        refresh_rate=float(str(_get("CASEDD_REFRESH_RATE", "refresh_rate", 2.0))),
        width=int(str(_get("CASEDD_WIDTH", "width", 800))),
        height=int(str(_get("CASEDD_HEIGHT", "height", 480))),
        templates_dir=Path(str(_get("CASEDD_TEMPLATES_DIR", "templates_dir", "templates"))),
        assets_dir=Path(str(_get("CASEDD_ASSETS_DIR", "assets_dir", "assets"))),
        disk_mount=str(_get("CASEDD_DISK_MOUNT", "disk_mount", "/")),
        viewer_bg=str(_get("CASEDD_VIEWER_BG", "viewer_bg", "#0d0f12")),
        speedtest_interval=float(
            str(_get("CASEDD_SPEEDTEST_INTERVAL", "speedtest_interval", 1800.0))
        ),
        speedtest_advertised_down_mbps=float(
            str(
                _get(
                    "CASEDD_SPEEDTEST_ADVERTISED_DOWN_MBPS",
                    "speedtest_advertised_down_mbps",
                    2000.0,
                )
            )
        ),
        speedtest_advertised_up_mbps=float(
            str(
                _get(
                    "CASEDD_SPEEDTEST_ADVERTISED_UP_MBPS",
                    "speedtest_advertised_up_mbps",
                    200.0,
                )
            )
        ),
        speedtest_reference_down_mbps=_get_optional_float(
            "CASEDD_SPEEDTEST_REFERENCE_DOWN_MBPS",
            "speedtest_reference_down_mbps",
        ),
        speedtest_reference_up_mbps=_get_optional_float(
            "CASEDD_SPEEDTEST_REFERENCE_UP_MBPS",
            "speedtest_reference_up_mbps",
        ),
        speedtest_marginal_ratio=float(
            str(_get("CASEDD_SPEEDTEST_MARGINAL_RATIO", "speedtest_marginal_ratio", 0.9))
        ),
        speedtest_critical_ratio=float(
            str(_get("CASEDD_SPEEDTEST_CRITICAL_RATIO", "speedtest_critical_ratio", 0.7))
        ),
        speedtest_binary=str(_get("CASEDD_SPEEDTEST_BINARY", "speedtest_binary", "speedtest")),
        speedtest_server_id=str(
            _get("CASEDD_SPEEDTEST_SERVER_ID", "speedtest_server_id", "")
        )
        or None,
        ollama_api_base=str(_get("CASEDD_OLLAMA_API_BASE", "ollama_api_base", "http://localhost:11434")),
        ollama_interval=float(str(_get("CASEDD_OLLAMA_INTERVAL", "ollama_interval", 10.0))),
        ollama_timeout=float(str(_get("CASEDD_OLLAMA_TIMEOUT", "ollama_timeout", 3.0))),
    )
