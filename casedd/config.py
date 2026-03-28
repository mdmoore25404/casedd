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
import re
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.dataclasses import dataclass
import yaml

_HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class TemplateScheduleRule(BaseModel):
    """Time-window rule for template selection.

    Attributes:
        template: Template name selected when this schedule matches.
        start: Start time in ``HH:MM`` 24-hour local time.
        end: End time in ``HH:MM`` 24-hour local time.
        days: Optional weekdays where rule applies (0=Mon ... 6=Sun).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    template: str
    start: str
    end: str
    days: list[int] = Field(default_factory=list)

    @field_validator("start", "end")
    @classmethod
    def _validate_hhmm(cls, value: str) -> str:
        """Validate 24-hour ``HH:MM`` format.

        Args:
            value: Raw time string.

        Returns:
            Normalized time string.

        Raises:
            ValueError: If the time is not in ``HH:MM`` format.
        """
        if _HHMM_PATTERN.match(value) is None:
            msg = f"time must be HH:MM (24-hour), got '{value}'"
            raise ValueError(msg)
        return value

    @field_validator("days")
    @classmethod
    def _validate_days(cls, value: list[int]) -> list[int]:
        """Validate weekday indexes.

        Args:
            value: Weekday indexes (0=Mon ... 6=Sun).

        Returns:
            Validated weekday list.

        Raises:
            ValueError: If any day is outside 0..6.
        """
        invalid = [day for day in value if day < 0 or day > 6]
        if invalid:
            msg = f"days entries must be in range 0..6, got {invalid}"
            raise ValueError(msg)
        return value


class TemplateTriggerRule(BaseModel):
    """Data-driven trigger rule for template selection.

    Attributes:
        source: Dotted data-store key to inspect.
        operator: Comparison operator token.
        value: Threshold value for comparison.
        template: Template selected when condition is satisfied.
        duration: Seconds condition must remain true before activating.
        hold_for: Minimum seconds to keep this template active once triggered.
        clear_operator: Optional explicit operator used to clear an active trigger.
        clear_value: Optional explicit threshold used with clear_operator.
        cooldown: Seconds before the same trigger may activate again.
        priority: Lower number = higher priority when multiple triggers match.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    source: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "neq"] = "gte"
    value: float | int | str
    template: str
    duration: float = Field(default=0.0, ge=0.0)
    hold_for: float = Field(default=0.0, ge=0.0)
    clear_operator: Literal["gt", "gte", "lt", "lte", "eq", "neq"] | None = None
    clear_value: float | int | str | None = None
    cooldown: float = Field(default=0.0, ge=0.0)
    priority: int = Field(default=100, ge=0, le=1000)

    @model_validator(mode="after")
    def _validate_clear_rule(self) -> "TemplateTriggerRule":
        """Validate clear-rule pair semantics.

        Returns:
            Self after validation.

        Raises:
            ValueError: If only one clear-rule field is provided.
        """
        has_clear_operator = self.clear_operator is not None
        has_clear_value = self.clear_value is not None
        if has_clear_operator != has_clear_value:
            msg = "clear_operator and clear_value must be set together"
            raise ValueError(msg)
        return self


class PanelConfig(BaseModel):
    """Configuration for one output panel/framebuffer.

    Attributes:
        name: Stable panel identifier.
        display_name: Human-friendly panel name for UI selectors.
        fb_device: Optional framebuffer path for this panel.
        no_fb: Optional per-panel framebuffer disable flag.
        width: Optional panel width override in pixels.
        height: Optional panel height override in pixels.
        template: Optional per-panel base template name.
        template_rotation: Optional per-panel rotation templates.
        template_rotation_interval: Optional per-panel rotation interval seconds.
        template_schedule: Optional per-panel schedule rules.
        template_triggers: Optional per-panel trigger rules.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: str
    display_name: str | None = None
    fb_device: Path | None = None
    no_fb: bool | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    template: str | None = None
    template_rotation: list[str] = Field(default_factory=list)
    template_rotation_interval: float | None = Field(default=None, gt=0)
    template_schedule: list[TemplateScheduleRule] = Field(default_factory=list)
    template_triggers: list[TemplateTriggerRule] = Field(default_factory=list)


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
        procfs_path: Linux procfs root path used by psutil.
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
        htop_interval: Process table polling interval in seconds.
        htop_max_rows: Maximum process rows for htop-style widget.
        weather_provider: Weather provider identifier (nws/open-meteo).
        weather_interval: Weather polling interval in seconds.
        weather_zipcode: Optional US zipcode used for location lookup.
        weather_lat: Optional latitude override for weather polling.
        weather_lon: Optional longitude override for weather polling.
        weather_user_agent: User-Agent header sent to weather APIs.
        ollama_api_base: Base URL for Ollama HTTP API.
        ollama_interval: Ollama polling interval in seconds.
        ollama_timeout: Ollama request timeout in seconds.
        ups_interval: UPS polling interval in seconds.
        ups_command: Optional custom UPS command override.
        ups_upsc_target: Target argument for ``upsc`` fallback mode.
        net_interfaces: Explicit network interface names to monitor (e.g.
            ``["enp8s0"]``). Traffic from all other interfaces (Docker bridges,
            veth pairs, loopback) is excluded. Empty list falls back to the
            psutil aggregate across all interfaces.
        template_rotation: Additional template names to cycle through.
        template_rotation_interval: Seconds spent on each rotated template.
        template_schedule: Local-time schedule rules overriding rotation.
        template_triggers: Data-value trigger rules overriding schedule/rotation.
        panels: Optional per-panel output/runtime definitions.
        always_collect_prefixes: Namespaces that are always sampled.
        test_mode: Disable all getters globally when true.
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
    procfs_path: str = Field(default="/proc")
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
    htop_interval: float = Field(default=2.0)
    htop_max_rows: int = Field(default=12, ge=1, le=40)
    weather_provider: str = Field(default="nws")
    weather_interval: float = Field(default=300.0)
    weather_zipcode: str | None = Field(default=None)
    weather_lat: float | None = Field(default=None)
    weather_lon: float | None = Field(default=None)
    weather_user_agent: str = Field(
        default="CASEDD/0.2 (https://github.com/casedd/casedd)",
    )
    ollama_api_base: str = Field(default="http://localhost:11434")
    ollama_interval: float = Field(default=10.0)
    ollama_timeout: float = Field(default=3.0)
    ups_interval: float = Field(default=5.0)
    ups_command: str | None = Field(default=None)
    ups_upsc_target: str = Field(default="ups@localhost")
    net_interfaces: list[str] = Field(default_factory=list)
    template_rotation: list[str] = Field(default_factory=list)
    template_rotation_interval: float = Field(default=30.0)
    template_schedule: list[TemplateScheduleRule] = Field(default_factory=list)
    template_triggers: list[TemplateTriggerRule] = Field(default_factory=list)
    panels: list[PanelConfig] = Field(default_factory=list)
    always_collect_prefixes: list[str] = Field(default_factory=list)
    test_mode: bool = Field(default=False)

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

    @field_validator("ups_interval")
    @classmethod
    def _validate_ups_interval(cls, v: float) -> float:
        """Ensure UPS polling interval is positive and practical.

        Args:
            v: Poll interval seconds.

        Returns:
            Validated interval value.

        Raises:
            ValueError: If interval is outside accepted bounds.
        """
        if not (1.0 <= v <= 3600.0):
            msg = f"ups_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("template_rotation_interval")
    @classmethod
    def _validate_template_rotation_interval(cls, v: float) -> float:
        """Ensure rotation interval is positive and practical.

        Args:
            v: Rotation interval in seconds.

        Returns:
            Validated interval.

        Raises:
            ValueError: If interval is outside accepted bounds.
        """
        if not (1.0 <= v <= 86400.0):
            msg = f"template_rotation_interval must be between 1 and 86400, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("always_collect_prefixes")
    @classmethod
    def _validate_always_collect_prefixes(cls, value: list[str]) -> list[str]:
        """Normalize always-on source namespace prefixes.

        Args:
            value: Raw prefix list from env/yaml.

        Returns:
            Normalized and deduplicated prefix list.
        """
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = item.strip().lower().rstrip(".")
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append(name)
        return normalized


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

    def _get_rotation_templates() -> list[str]:
        """Parse template rotation list from env or YAML.

        Returns:
            Ordered list of template names.
        """
        raw = _get("CASEDD_TEMPLATE_ROTATION", "template_rotation", [])
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        text = str(raw)
        return [item.strip() for item in text.split(",") if item.strip()]

    def _get_yaml_list(key: str) -> list[object]:
        """Read a list value from YAML with safe fallback.

        Args:
            key: Top-level YAML key.

        Returns:
            List value or empty list.
        """
        raw = yaml_data.get(key, [])
        if isinstance(raw, list):
            return raw
        return []

    def _get_always_collect_prefixes() -> list[str]:
        """Parse always-on getter categories from env/yaml.

        Returns:
            Namespace prefix list.
        """
        raw = _get("CASEDD_ALWAYS_COLLECT_PREFIXES", "always_collect_prefixes", [])
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        text = str(raw)
        return [item.strip() for item in text.split(",") if item.strip()]

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
        procfs_path=str(_get("CASEDD_PROCFS_PATH", "procfs_path", "/proc")),
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
        htop_interval=float(str(_get("CASEDD_HTOP_INTERVAL", "htop_interval", 2.0))),
        htop_max_rows=int(str(_get("CASEDD_HTOP_MAX_ROWS", "htop_max_rows", 12))),
        weather_provider=str(_get("CASEDD_WEATHER_PROVIDER", "weather_provider", "nws")),
        weather_interval=float(
            str(_get("CASEDD_WEATHER_INTERVAL", "weather_interval", 300.0))
        ),
        weather_zipcode=str(_get("CASEDD_WEATHER_ZIPCODE", "weather_zipcode", "")).strip()
        or None,
        weather_lat=_get_optional_float("CASEDD_WEATHER_LAT", "weather_lat"),
        weather_lon=_get_optional_float("CASEDD_WEATHER_LON", "weather_lon"),
        weather_user_agent=str(
            _get(
                "CASEDD_WEATHER_USER_AGENT",
                "weather_user_agent",
                "CASEDD/0.2 (https://github.com/casedd/casedd)",
            )
        ),
        ollama_api_base=str(_get("CASEDD_OLLAMA_API_BASE", "ollama_api_base", "http://localhost:11434")),
        ollama_interval=float(str(_get("CASEDD_OLLAMA_INTERVAL", "ollama_interval", 10.0))),
        ollama_timeout=float(str(_get("CASEDD_OLLAMA_TIMEOUT", "ollama_timeout", 3.0))),
        ups_interval=float(str(_get("CASEDD_UPS_INTERVAL", "ups_interval", 5.0))),
        ups_command=str(_get("CASEDD_UPS_COMMAND", "ups_command", "")).strip() or None,
        ups_upsc_target=str(_get("CASEDD_UPS_UPSC_TARGET", "ups_upsc_target", "ups@localhost")),
        net_interfaces=[
            iface.strip()
            for iface in str(_get("CASEDD_NET_INTERFACES", "net_interfaces", "")).split(",")
            if iface.strip()
        ],
        template_rotation=_get_rotation_templates(),
        template_rotation_interval=float(
            str(_get("CASEDD_TEMPLATE_ROTATION_INTERVAL", "template_rotation_interval", 30.0))
        ),
        template_schedule=cast(
            "list[TemplateScheduleRule]",
            _get_yaml_list("template_schedule"),
        ),
        template_triggers=cast(
            "list[TemplateTriggerRule]",
            _get_yaml_list("template_triggers"),
        ),
        panels=cast("list[PanelConfig]", _get_yaml_list("panels")),
        always_collect_prefixes=_get_always_collect_prefixes(),
        test_mode=str(_get("CASEDD_TEST_MODE", "test_mode", "0"))
        not in {"0", "false", "False", ""},
    )
