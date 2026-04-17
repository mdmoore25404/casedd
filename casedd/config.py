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
from typing import Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.dataclasses import dataclass
import yaml

_HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SportsTeamConfig(BaseModel):
    """One followed sports team entry.

    Attributes:
        team: Team name as it appears in TheSportsDB (e.g. ``"Los Angeles Lakers"``).
            Must match the TheSportsDB search results closely.  Use the name
            returned by ``searchteams.php`` when unsure.
        sport: Optional sport hint for disambiguation (e.g. ``"NBA"``,
            ``"American Football"``).  Required when a team name matches
            entries across multiple sports (e.g. ``"West Virginia"``).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    team: str
    sport: str = ""


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
        notify: Send a Pushover webhook notification when this trigger activates.
        notify_title: Optional custom notification title (default: source key name).
        notify_message: Optional custom notification body (default: auto-generated).
        disabled: When true the rule is parsed but never evaluated.  Use this
            to temporarily suppress a trigger without removing it from the
            config file.
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
    notify: bool = False
    notify_title: str | None = None
    notify_message: str | None = None
    disabled: bool = False

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


class RotationSkipCondition(BaseModel):
    """One condition that causes a rotation entry to be skipped.

    All conditions in a ``skip_if`` list must match (AND semantics) for the
    entry to be skipped.  When the ``source`` key is absent from the data
    store the condition evaluates to ``True`` (skip the template) so that
    templates whose data has never arrived are not shown.

    Attributes:
        source: Dotted data-store key to inspect.
        operator: Comparison operator.
        value: Threshold value.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    source: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "neq"] = "lte"
    value: float | int | str = 0


class RotationEntry(BaseModel):
    """One template entry in a rotation sequence.

    Attributes:
        template: Template name to display.
        seconds: Dwell time in seconds.  ``None`` means use the panel's
            default rotation interval.
        skip_if: Conditions that must all be true for this entry to be
            skipped.  An empty list means the entry is never skipped.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    template: str
    seconds: float | None = Field(default=None, gt=0)
    skip_if: list[RotationSkipCondition] = Field(default_factory=list)


class ViewerLayout(BaseModel):
    """Grid layout for the multi-panel web viewer.

    Defines how panels are arranged in the viewer's grid view.  When
    ``viewer_layout`` is present in ``casedd.yaml`` the web UI uses this
    layout instead of the auto-fit fallback, giving operators full control
    over which panel appears in which cell.

    The ``cells`` list maps left-to-right, top-to-bottom into a
    ``columns``-wide CSS grid.  Empty strings (``""``) leave a cell blank.

    Example YAML (2-column, 2-row grid)::

        viewer_layout:
          columns: 2
          cells:
            - primary     # row 1, col 1
            - virtpanel   # row 1, col 2
            - virtpanel2  # row 2, col 1
            - ""          # row 2, col 2 — intentionally empty

    Attributes:
        columns: Number of grid columns.  Must be between 1 and 16.
        cells: Panel names in grid order (empty string = blank cell).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    columns: int = Field(default=2, ge=1, le=16)
    cells: list[str] = Field(default_factory=list)


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
        template_rotation: Optional per-panel rotation templates. Accepts
            either template names or :class:`RotationEntry` objects with
            per-template dwell times.
        template_rotation_interval: Optional per-panel rotation interval seconds.
        template_rotation_enabled: Optional per-panel rotation enable flag.
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
    template_rotation: list[str | RotationEntry] = Field(default_factory=list)
    template_rotation_interval: float | None = Field(default=None, gt=0)
    template_rotation_enabled: bool | None = None
    template_schedule: list[TemplateScheduleRule] = Field(default_factory=list)
    template_triggers: list[TemplateTriggerRule] = Field(default_factory=list)
    rotation: int | None = None

    @field_validator("rotation")
    @classmethod
    def _validate_rotation(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if int(v) not in {0, 90, 180, 270}:
            raise ValueError("rotation must be one of 0, 90, 180, 270")
        return int(v)


class OutputBackendConfig(BaseModel):
    """Configuration for one pluggable output backend.

    Used in the ``outputs:`` section of ``casedd.yaml`` to declare named
    output backends with independent resolution, refresh rate, and template
    settings.  All backends share the same data store (no redundant polling).

    Example YAML::

        outputs:
          usb_display:
            type: framebuffer
            device: /dev/fb1
            width: 800
            height: 480
            template: system_stats
          remote_view:
            type: websocket
            port: 8765
            template: system_stats

    Attributes:
        type: Backend type identifier; must match a registered
            :class:`~casedd.outputs.registry.OutputRegistry` entry.
            Built-in values: ``"framebuffer"``, ``"websocket"``.
        enabled: When ``False`` the backend is skipped at startup.
        display_name: Human-friendly label used in the HTTP viewer and logs.
            Defaults to the backend's YAML key name.
        width: Canvas width in pixels. Falls back to the global ``width``
            setting when omitted.
        height: Canvas height in pixels. Falls back to the global ``height``
            setting when omitted.
        template: Template name to render for this backend.  Falls back to
            the global ``template`` setting when omitted.
        refresh_rate: Render frequency in Hz.  Falls back to the global
            ``refresh_rate`` when omitted.
        device: Framebuffer device path (``framebuffer`` type only).
            Falls back to the global ``fb_device`` setting when omitted.
        rotation: Display rotation in degrees (0, 90, 180, 270).  Falls back
            to the global ``fb_rotation`` setting.  Applies to
            ``framebuffer`` backends only.
        port: TCP port for the WebSocket server (``websocket`` type only).
            Falls back to the global ``ws_port`` setting when omitted.
    """

    # non-strict so dicts coerce to Path etc. when loaded from raw YAML
    model_config = ConfigDict(strict=False, frozen=True, extra="ignore")

    type: str
    enabled: bool = True
    display_name: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    template: str | None = None
    refresh_rate: float | None = Field(default=None, gt=0)
    # framebuffer-specific
    device: Path | None = None
    rotation: int | None = None
    # websocket-specific
    port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("rotation")
    @classmethod
    def _validate_rotation(cls, v: int | None) -> int | None:
        """Check rotation is a valid degree value.

        Args:
            v: Raw rotation integer.

        Returns:
            Validated rotation value.

        Raises:
            ValueError: If the value is not one of 0, 90, 180, 270.
        """
        if v is None:
            return None
        if int(v) not in {0, 90, 180, 270}:
            raise ValueError("rotation must be one of 0, 90, 180, 270")
        return int(v)


class TuyaDeviceConfig(BaseModel):
    """Configuration for a single Tuya smart home device.

    Attributes:
        device_id: Tuya device ID (visible in Tuya app device details).
        local_key: Device local key (paired via Tuya app or obtained from
            cloud sync). Required for local network protocol.
        device_type: Device category — either ``sensor`` (temperature/humidity)
            or ``plug`` (smart outlet with power monitoring).
        ip_address: Optional local network IP address for faster polling.
            When omitted, devices are discovered via mDNS or cloud sync.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    device_id: str
    local_key: str
    device_type: str
    ip_address: str | None = Field(default=None)

    @field_validator("device_type")
    @classmethod
    def _validate_device_type(cls, value: str) -> str:
        """Ensure device_type is a recognized category.

        Args:
            value: Device type string.

        Returns:
            Validated type.

        Raises:
            ValueError: If type is not ``sensor`` or ``plug``.
        """
        if value not in {"sensor", "plug"}:
            msg = f"device_type must be 'sensor' or 'plug', got '{value}'"
            raise ValueError(msg)
        return value


@dataclass(config=ConfigDict(frozen=True))
class Config:
    """Daemon-wide configuration.

    Attributes:
        log_level: Logging verbosity (NONE, DEBUG, INFO, WARNING, ERROR, CRITICAL).
        debug_perf_metrics: Enable per-tick hot-path timing logs (render, output,
            encode).  Set ``CASEDD_DEBUG_PERF_METRICS=1`` or add
            ``debug_perf_metrics: true`` to ``casedd.yaml``.  Should only be
            used during development; adds ``time.perf_counter()`` calls on
            every render tick.  Disabled in production via systemd.
        debug_frame_logs: Enable per-frame renderer debug logs (hot path);
            defaults to ``False`` to avoid unnecessary CPU/log overhead.
        no_fb: Disable framebuffer output entirely (dev / no-hardware mode).
        fb_device: Path to the framebuffer device file.
        fb_auto_detect: Scan for USB framebuffer displays at startup; uses the
            first detected USB display when the configured ``fb_device`` is
            absent.  Resolution is derived from the display when not explicitly
            overridden via ``width`` / ``height``.
        ws_port: WebSocket server port.
        http_port: HTTP viewer / API port.
        socket_path: Unix domain socket path for JSON data-write pushes.
        template: Active template name (no extension; relative to ``templates/``).
        startup_frame_seconds: Seconds to display the startup splash before
            normal rendering begins, allowing getters time to populate data.
        refresh_rate: Render frequency in Hz.
        width: Canvas width in pixels.
        height: Canvas height in pixels.
        templates_dir: Directory containing ``.casedd`` template files.
        assets_dir: Directory containing static assets.
        procfs_path: Linux procfs root path used by psutil.
        disk_mount: Filesystem mount point to monitor for disk metrics.
        viewer_bg: Default browser viewer page background color.
        cpu_interval: CPU usage / temperature polling interval in seconds (default: 2.0).
        gpu_interval: GPU metrics (nvidia-smi) polling interval in seconds (default: 5.0).
        memory_interval: Memory usage polling interval in seconds (default: 2.0).
        disk_interval: Disk usage / I/O polling interval in seconds (default: 2.0).
        network_interval: Network throughput polling interval in seconds (default: 2.0).
        system_interval: Host metadata (hostname, uptime, load) polling interval in
            seconds (default: 10.0).
        fans_interval: Fan speed polling interval in seconds (default: 3.0).
        net_ports_interval: Open network ports polling interval in seconds (default: 5.0).
        sysinfo_interval: Static system info polling interval in seconds (default: 30.0).
        containers_interval: Container runtime polling interval in seconds.
        containers_runtime: Preferred runtime backend (auto/docker/podman/containerd).
        containers_max_items: Maximum indexed ``containers.<n>`` rows emitted.
        speedtest_interval: Interval between speed tests in seconds.
        speedtest_startup_delay: Delay before first speed test run in seconds.
        speedtest_advertised_down_mbps: Advertised download speed in Mb/s.
        speedtest_advertised_up_mbps: Advertised upload speed in Mb/s.
        speedtest_reference_down_mbps: Optional effective downlink baseline in Mb/s.
        speedtest_reference_up_mbps: Optional effective uplink baseline in Mb/s.
        speedtest_marginal_ratio: Ratio under which speeds are considered marginal.
        speedtest_critical_ratio: Ratio under which speeds are considered critical.
        display_padding: Padding in pixels applied between the physical display
            edge and the rendered content area.  Accepts a single integer (all
            four sides) or a list of two ([vertical, horizontal]) or four
            ([top, right, bottom, left]) integers.  The surrounding area is
            filled with the template background colour.  Defaults to ``0``
            (no padding).  Useful when the monitor bezel clips the image edges
            or when a visual margin is desired.
        speedtest_passive: When true, disable the local CLI poller entirely and
            accept speed results only via ``POST /api/update``.  Use this when
            another machine on the network runs the actual speed test and pushes
            results via the REST ingestion endpoint.
        speedtest_cache_path: Local JSON cache file used to persist the latest
            ``speedtest.*`` values across daemon restarts.
        speedtest_cache_max_age_hours: Maximum age in hours for cached speedtest
            data restored at startup. Older cache snapshots are ignored.
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
        os_updates_interval: OS package-update polling interval in seconds.
        os_updates_manager: Package manager mode (auto/apt/dnf).
        invokeai_base_url: InvokeAI API base URL.
        invokeai_api_token: Optional InvokeAI API token for bearer auth.
        invokeai_interval: InvokeAI polling interval in seconds.
        invokeai_timeout: InvokeAI HTTP request timeout in seconds.
        invokeai_verify_tls: Verify InvokeAI HTTPS certificates when true.
        ollama_api_base: Base URL for Ollama HTTP API.
        ollama_interval: Ollama polling interval in seconds.
        ollama_timeout: Ollama request timeout in seconds.
        ollama_detailed: Enable optional detailed polling (/api/version,/api/tags).
        ollama_detail_max_models: Maximum running/local model entries emitted.
        ups_interval: UPS polling interval in seconds.
        ups_command: Optional custom UPS command override.
        ups_upsc_target: Target argument for ``upsc`` fallback mode.
        vms_interval: KVM/libvirt VM polling interval in seconds.
        vms_passive: Disable local virsh polling and expect external pushes.
        vms_command: Virsh binary name or absolute path.
        vms_max_items: Maximum indexed ``vms.<n>`` rows emitted.
        pihole_base_url: Pi-hole API base URL.
        radarr_base_url: Radarr API base URL.
        radarr_api_key: Radarr API key for X-Api-Key auth.
        radarr_interval: Radarr polling interval in seconds.
        radarr_timeout: Radarr HTTP request timeout in seconds.
        radarr_calendar_days: Radarr look-ahead days for upcoming items.
        radarr_verify_tls: Verify Radarr HTTPS certificates when true.
        sonarr_base_url: Sonarr API base URL.
        sonarr_api_key: Sonarr API key for X-Api-Key auth.
        sonarr_interval: Sonarr polling interval in seconds.
        sonarr_timeout: Sonarr HTTP request timeout in seconds.
        sonarr_calendar_days: Sonarr look-ahead days for upcoming items.
        sonarr_verify_tls: Verify Sonarr HTTPS certificates when true.
        pihole_api_token: Pi-hole API token for Authorization bearer auth.
        pihole_password: Pi-hole app/web password used with ``POST /api/auth``
            to obtain a session SID.
        pihole_session_sid: Optional Pi-hole session SID for direct API auth.
        pihole_timeout: Pi-hole HTTP request timeout in seconds.
        pihole_verify_tls: Verify Pi-hole HTTPS certificates when true.
        pihole_interval: Pi-hole polling interval in seconds.
        plex_base_url: Plex server base URL.
        plex_token: Plex API token for authenticated requests.
        plex_client_identifier: Client identifier sent as X-Plex-Client-Identifier.
        plex_product: Product name sent as X-Plex-Product.
        plex_timeout: Plex HTTP timeout in seconds.
        plex_verify_tls: Verify Plex HTTPS certificates when true.
        plex_interval: Plex polling interval in seconds.
        plex_max_sessions: Maximum now-playing rows to emit.
        plex_max_recent: Maximum recently-added rows to emit.
        plex_privacy_filter_regex: Optional regex used to redact media and
            library names from Plex payloads.
        plex_privacy_filter_libraries: Optional library names to redact,
            matched case-insensitively.
        plex_privacy_redaction_text: Replacement text for redacted values.
        net_interfaces: Explicit network interface names to monitor (e.g.
            ``["enp8s0"]``). Traffic from all other interfaces (Docker bridges,
            veth pairs, loopback) is excluded. Empty list falls back to the
            psutil aggregate across all interfaces.
        nzbget_url: NZBGet API server URL.
        nzbget_username: Optional username for NZBGet RPC authentication.
        nzbget_password: Optional password for NZBGet RPC authentication.
        nzbget_interval: NZBGet polling interval in seconds.
        nzbget_timeout: NZBGet HTTP request timeout in seconds.
        sabnzbd_base_url: SABnzbd server base URL.  Leave empty to disable
            the getter entirely.
        sabnzbd_api_key: SABnzbd API key for authentication.
        sabnzbd_interval: SABnzbd polling interval in seconds.
        sabnzbd_timeout: SABnzbd HTTP request timeout in seconds.
        sabnzbd_verify_tls: Verify TLS certificates for HTTPS SABnzbd URLs.
        sabnzbd_max_slots: Maximum active queue slot rows to flatten.
        jellyfin_base_url: Jellyfin server base URL.  Leave empty to disable
            the getter entirely.
        jellyfin_api_key: Jellyfin API key (Dashboard → API Keys).
        jellyfin_interval: Jellyfin polling interval in seconds.
        jellyfin_timeout: Jellyfin HTTP request timeout in seconds.
        jellyfin_verify_tls: Verify TLS certificates for HTTPS Jellyfin URLs.
        jellyfin_max_sessions: Maximum active session rows to flatten.
        sports_enabled: Enable the sports scores getter (TheSportsDB API).
        sports_api_key: TheSportsDB API key.  The public free key ``"123"``
            requires no registration.  Premium keys available at
            https://www.thesportsdb.com/pricing.
        sports_interval: Sports data polling interval in seconds (default: 300).
        sports_timeout: HTTP request timeout in seconds.
        sports_max_teams: Maximum followed-team slots indexed (default: 10).
        sports_recent_window_hours: Window in hours for showing recent results.
        sports_followed_teams: Teams to follow.  Each entry has a ``team``
            name and optional ``sport`` hint for disambiguation.
        template_rotation: Additional templates to cycle through. Accepts
            either template names or :class:`RotationEntry` objects with
            per-template dwell times.
        template_rotation_interval: Seconds spent on each rotated template.
        template_rotation_enabled: Enables/disables template rotation.
            When ``False``, only ``template`` is shown (unless a trigger or
            schedule rule overrides it).
        template_schedule: Local-time schedule rules overriding rotation.
        template_triggers: Data-value trigger rules overriding schedule/rotation.
        trigger_border_color: Border color painted around trigger-held frames.
            Any CSS color string accepted by the renderer (hex, named, rgb()).
            Defaults to bright red (``"#dc1e1e"``).  Override if red is not
            accessible for your display environment (e.g. ``"#ff00ff"`` for
            magenta / fuchsia).
        panels: Optional per-panel output/runtime definitions.
        viewer_layout: Optional grid layout for the multi-panel web viewer.
            Defines column count and which panel appears in each cell.
            When omitted the viewer uses an auto-fit grid.
        outputs: Pluggable output backend definitions.  When present, the
            daemon builds backends from this mapping and routes rendered
            frames to each enabled backend.  Keys are stable backend names;
            values specify type, resolution, template, and other per-backend
            settings.  Falls back to the ``panels`` / legacy single-panel
            system when this mapping is empty (default).
        always_collect_prefixes: Namespaces that are always sampled.
        pushover_webhook_url: Pushover webhook URL for trigger notifications.
            Create a webhook at https://pushover.net/dashboard and paste its
            URL here.  When a trigger rule with ``notify: true`` activates,
            CASEDD posts a JSON payload to this URL.
        test_mode: Disable all getters globally when true.
        api_key: Optional shared secret for the ``POST /api/update`` endpoint.
            When set, all update requests must include an ``X-API-Key`` header
            matching this value.  Leave unset (default) to allow unauthenticated
            pushes (suitable for trusted LAN deployments).
        api_basic_user: Optional HTTP Basic Auth username for update endpoints.
            When set together with ``api_basic_password``, update requests may
            authenticate with an ``Authorization: Basic ...`` header.
        api_basic_password: Optional HTTP Basic Auth password for update endpoints.
            Must be configured together with ``api_basic_user``.
        api_rate_limit: Maximum update requests per minute accepted from a
            single source IP.  ``0`` (default) disables rate limiting.
    """

    log_level: str = Field(default="INFO")
    debug_frame_logs: bool = Field(default=False)
    debug_perf_metrics: bool = Field(default=False)
    no_fb: bool = Field(default=False)
    fb_device: Path = Field(default=Path("/dev/fb1"))
    fb_auto_detect: bool = Field(default=False)
    fb_rotation: int = Field(default=0)
    # When true, CASEDD will claim the primary display at startup if no local
    # keyboard or mouse is attached. This avoids taking over a user's login
    # monitor when local input is present.
    fb_claim_on_no_input: bool = Field(default=False)
    ws_port: int = Field(default=8765)
    http_port: int = Field(default=8080)
    socket_path: Path = Field(default=Path("/run/casedd/casedd.sock"))
    template: str = Field(default="system_stats")
    startup_frame_seconds: float = Field(default=5.0)
    refresh_rate: float = Field(default=2.0)
    width: int = Field(default=800)
    height: int = Field(default=480)
    templates_dir: Path = Field(default=Path("templates"))
    assets_dir: Path = Field(default=Path("assets"))
    procfs_path: str = Field(default="/proc")
    disk_mount: str = Field(default="/")
    viewer_bg: str = Field(default="#0d0f12")
    display_padding: int | list[int] = Field(default=0)
    cpu_interval: float = Field(default=2.0, gt=0)
    gpu_interval: float = Field(default=5.0, gt=0)
    memory_interval: float = Field(default=2.0, gt=0)
    disk_interval: float = Field(default=2.0, gt=0)
    network_interval: float = Field(default=2.0, gt=0)
    system_interval: float = Field(default=10.0, gt=0)
    fans_interval: float = Field(default=3.0, gt=0)
    net_ports_interval: float = Field(default=5.0, gt=0)
    sysinfo_interval: float = Field(default=30.0, gt=0)
    containers_interval: float = Field(default=8.0)
    containers_runtime: str = Field(default="auto")
    containers_max_items: int = Field(default=12, ge=1, le=100)
    speedtest_interval: float = Field(default=1800.0)
    speedtest_startup_delay: float = Field(default=0.0)
    speedtest_advertised_down_mbps: float = Field(default=2000.0)
    speedtest_advertised_up_mbps: float = Field(default=200.0)
    speedtest_reference_down_mbps: float | None = Field(default=None)
    speedtest_reference_up_mbps: float | None = Field(default=None)
    speedtest_marginal_ratio: float = Field(default=0.9)
    speedtest_critical_ratio: float = Field(default=0.7)
    speedtest_passive: bool = Field(default=False)
    speedtest_cache_path: Path = Field(default=Path("run/speedtest-cache.json"))
    speedtest_cache_max_age_hours: float = Field(default=8.0)
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
    os_updates_interval: float = Field(default=900.0)
    os_updates_manager: str = Field(default="auto")
    invokeai_base_url: str = Field(default="http://localhost:9090")
    invokeai_api_token: str | None = Field(default=None, repr=False)
    invokeai_interval: float = Field(default=5.0)
    invokeai_timeout: float = Field(default=4.0)
    invokeai_verify_tls: bool = Field(default=True)
    ollama_api_base: str = Field(default="http://localhost:11434")
    ollama_interval: float = Field(default=10.0)
    ollama_timeout: float = Field(default=3.0)
    ollama_detailed: bool = Field(default=False)
    ollama_detail_max_models: int = Field(default=8, ge=1, le=100)
    ups_interval: float = Field(default=5.0)
    ups_command: str | None = Field(default=None)
    ups_upsc_target: str = Field(default="ups@localhost")
    vms_interval: float = Field(default=10.0)
    vms_passive: bool = Field(default=False)
    vms_command: str = Field(default="virsh")
    vms_max_items: int = Field(default=8, ge=1, le=50)
    radarr_base_url: str = Field(default="")
    radarr_api_key: str | None = Field(default=None, repr=False)
    radarr_interval: float = Field(default=15.0)
    radarr_timeout: float = Field(default=4.0)
    radarr_calendar_days: int = Field(default=7, ge=1, le=31)
    radarr_verify_tls: bool = Field(default=True)
    sonarr_base_url: str = Field(default="")
    sonarr_api_key: str | None = Field(default=None, repr=False)
    sonarr_interval: float = Field(default=15.0)
    sonarr_timeout: float = Field(default=4.0)
    sonarr_calendar_days: int = Field(default=7, ge=1, le=31)
    sonarr_verify_tls: bool = Field(default=True)
    pihole_base_url: str = Field(default="http://pi.hole")
    pihole_api_token: str | None = Field(default=None, repr=False)
    pihole_password: str | None = Field(default=None, repr=False)
    pihole_session_sid: str | None = Field(default=None, repr=False)
    pihole_timeout: float = Field(default=4.0)
    pihole_verify_tls: bool = Field(default=True)
    pihole_interval: float = Field(default=5.0)
    synology_host: str = Field(default="")
    synology_username: str | None = Field(default=None)
    synology_password: str | None = Field(default=None, repr=False)
    synology_sid: str | None = Field(default=None, repr=False)
    synology_interval: float = Field(default=20.0)
    synology_timeout: float = Field(default=5.0)
    synology_verify_tls: bool = Field(default=True)
    synology_volume_exclude_regex: str | None = Field(default=None)
    synology_user_exclude_regex: str | None = Field(default=None)
    synology_surveillance_enabled: bool = Field(default=True)
    synology_surveillance_max_cameras: int = Field(default=4, ge=0, le=16)
    synology_camera_snapshot_enabled: bool = Field(default=True)
    synology_camera_snapshot_width: int = Field(default=640, ge=0, le=4096)
    synology_camera_snapshot_height: int = Field(default=360, ge=0, le=4096)
    synology_camera_include_regex: str | None = Field(default=None)
    synology_camera_exclude_regex: str | None = Field(default=None)
    synology_camera_exclude_statuses: str = Field(default="7")
    synology_dsm_updates_enabled: bool = Field(default=True)
    synology_strip_domain_hostname: bool = Field(default=True)
    truenas_host: str = Field(default="")
    truenas_port: int = Field(default=80, ge=1, le=65535)
    truenas_api_key: str | None = Field(default=None, repr=False)
    truenas_interval: float = Field(default=10.0)
    truenas_timeout: float = Field(default=5.0)
    truenas_verify_tls: bool = Field(default=True)
    truenas_strip_domain_hostname: bool = Field(default=True)
    tuya_devices: list[TuyaDeviceConfig] = Field(default_factory=list)
    tuya_interval: float = Field(default=10.0)
    tuya_cloud_enabled: bool = Field(default=False)
    tuya_api_region: str = Field(default="")
    tuya_api_key: str | None = Field(default=None, repr=False)
    tuya_api_secret: str | None = Field(default=None, repr=False)
    tuya_api_device_id: str | None = Field(default=None)
    plex_base_url: str = Field(default="http://localhost:32400")
    plex_token: str | None = Field(default=None, repr=False)
    plex_client_identifier: str = Field(default="casedd")
    plex_product: str = Field(default="CASEDD")
    plex_timeout: float = Field(default=4.0)
    plex_verify_tls: bool = Field(default=True)
    plex_interval: float = Field(default=5.0)
    plex_max_sessions: int = Field(default=6, ge=1, le=20)
    plex_max_recent: int = Field(default=6, ge=1, le=20)
    plex_privacy_filter_regex: str | None = Field(default=None)
    plex_privacy_filter_libraries: list[str] = Field(default_factory=list)
    plex_privacy_redaction_text: str = Field(default="[hidden]")
    net_interfaces: list[str] = Field(default_factory=list)
    nzbget_url: str = Field(default="http://localhost:6789")
    nzbget_username: str | None = Field(default=None)
    nzbget_password: str | None = Field(default=None, repr=False)
    nzbget_interval: float = Field(default=5.0)
    nzbget_timeout: float = Field(default=3.0)
    nzbget_category_filter_regex: str | None = Field(default=None)
    sabnzbd_base_url: str = Field(default="")
    sabnzbd_api_key: str | None = Field(default=None, repr=False)
    sabnzbd_interval: float = Field(default=5.0)
    sabnzbd_timeout: float = Field(default=4.0)
    sabnzbd_verify_tls: bool = Field(default=True)
    sabnzbd_max_slots: int = Field(default=6, ge=1, le=20)
    jellyfin_base_url: str = Field(default="")
    jellyfin_api_key: str | None = Field(default=None, repr=False)
    jellyfin_interval: float = Field(default=5.0)
    jellyfin_timeout: float = Field(default=4.0)
    jellyfin_verify_tls: bool = Field(default=True)
    jellyfin_max_sessions: int = Field(default=6, ge=1, le=20)
    sports_enabled: bool = Field(default=False)
    sports_api_key: str = Field(default="123")
    sports_interval: float = Field(default=300.0, gt=0)
    sports_timeout: float = Field(default=5.0, gt=0)
    sports_max_teams: int = Field(default=10, ge=1, le=50)
    sports_recent_window_hours: int = Field(default=48, ge=1)
    sports_followed_teams: list[SportsTeamConfig] = Field(default_factory=list)
    nasa_api_key: str | None = Field(default=None, repr=False)
    apod_interval: float = Field(default=14400.0, gt=0)
    apod_cache_dir: str = Field(default="/tmp/casedd-apod")  # noqa: S108  # intentional: cache non-repo data
    pushover_webhook_url: str | None = Field(default=None, repr=False)
    template_rotation: list[str | RotationEntry] = Field(default_factory=list)
    template_rotation_interval: float = Field(default=30.0)
    template_rotation_enabled: bool = Field(default=True)
    template_schedule: list[TemplateScheduleRule] = Field(default_factory=list)
    template_triggers: list[TemplateTriggerRule] = Field(default_factory=list)
    trigger_border_color: str = Field(default="#dc1e1e")
    panels: list[PanelConfig] = Field(default_factory=list)
    viewer_layout: ViewerLayout | None = Field(default=None)
    outputs: dict[str, OutputBackendConfig] = Field(default_factory=dict)
    always_collect_prefixes: list[str] = Field(default_factory=list)
    test_mode: bool = Field(default=False)
    api_key: str | None = Field(default=None, repr=False)
    api_basic_user: str | None = Field(default=None)
    api_basic_password: str | None = Field(default=None, repr=False)
    api_rate_limit: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_api_basic_credentials(self) -> Self:
        """Ensure Basic Auth credentials are either both set or both unset."""
        has_user = self.api_basic_user is not None
        has_password = self.api_basic_password is not None
        if has_user != has_password:
            msg = "api_basic_user and api_basic_password must be set together"
            raise ValueError(msg)
        return self

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
        valid = {"NONE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
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

    @field_validator("containers_interval")
    @classmethod
    def _validate_containers_interval(cls, v: float) -> float:
        """Ensure container polling interval is positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"containers_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("containers_runtime")
    @classmethod
    def _validate_containers_runtime(cls, value: str) -> str:
        """Validate preferred container runtime selector."""
        normalized = value.strip().lower()
        valid = {"auto", "docker", "podman", "containerd"}
        if normalized not in valid:
            msg = f"containers_runtime must be one of {sorted(valid)}, got {value!r}"
            raise ValueError(msg)
        return normalized

    @field_validator("startup_frame_seconds")
    @classmethod
    def _validate_startup_frame_seconds(cls, v: float) -> float:
        """Ensure startup splash duration is non-negative and sensible.

        Args:
            v: Startup splash duration in seconds.

        Returns:
            Validated duration.

        Raises:
            ValueError: If duration is outside accepted bounds.
        """
        if not (0.0 <= v <= 300.0):
            msg = f"startup_frame_seconds must be between 0 and 300 seconds, got {v}"
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

    @field_validator("speedtest_startup_delay")
    @classmethod
    def _validate_speedtest_startup_delay(cls, v: float) -> float:
        """Ensure startup delay is non-negative and practical.

        Args:
            v: Delay in seconds before first speedtest run.

        Returns:
            Validated delay.

        Raises:
            ValueError: If delay is outside accepted bounds.
        """
        if not (0.0 <= v <= 86400.0):
            msg = f"speedtest_startup_delay must be between 0 and 86400 seconds, got {v}"
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

    @field_validator("speedtest_cache_max_age_hours")
    @classmethod
    def _validate_speedtest_cache_max_age_hours(cls, v: float) -> float:
        """Ensure speedtest cache max-age is positive and bounded.

        Args:
            v: Maximum cache age in hours.

        Returns:
            Validated max-age value.

        Raises:
            ValueError: If max-age is outside accepted bounds.
        """
        if not (0.1 <= v <= 720.0):
            msg = f"speedtest_cache_max_age_hours must be between 0.1 and 720, got {v}"
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

    @field_validator("invokeai_interval")
    @classmethod
    def _validate_invokeai_interval(cls, v: float) -> float:
        """Ensure InvokeAI polling interval is positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"invokeai_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("os_updates_interval")
    @classmethod
    def _validate_os_updates_interval(cls, v: float) -> float:
        """Ensure OS updates polling interval is positive and practical."""
        if not (10.0 <= v <= 86400.0):
            msg = f"os_updates_interval must be between 10 and 86400 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("os_updates_manager")
    @classmethod
    def _validate_os_updates_manager(cls, v: str) -> str:
        """Normalize and validate package manager mode."""
        normalized = v.strip().lower()
        valid = {"auto", "apt", "dnf"}
        if normalized not in valid:
            msg = f"os_updates_manager must be one of {sorted(valid)}, got {v!r}"
            raise ValueError(msg)
        return normalized

    @field_validator("invokeai_timeout")
    @classmethod
    def _validate_invokeai_timeout(cls, v: float) -> float:
        """Ensure InvokeAI timeout is a positive value."""
        if v <= 0.0:
            msg = f"invokeai_timeout must be > 0, got {v}"
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

    @field_validator("ollama_detail_max_models")
    @classmethod
    def _validate_ollama_detail_max_models(cls, v: int) -> int:
        """Ensure detailed Ollama key expansion remains bounded.

        Args:
            v: Max running/local model entries to emit.

        Returns:
            Validated model-entry cap.

        Raises:
            ValueError: If value is outside accepted bounds.
        """
        if not (1 <= v <= 100):
            msg = f"ollama_detail_max_models must be between 1 and 100, got {v}"
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

    @field_validator("vms_interval")
    @classmethod
    def _validate_vms_interval(cls, v: float) -> float:
        """Ensure VM polling interval is positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"vms_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("vms_command")
    @classmethod
    def _validate_vms_command(cls, value: str) -> str:
        """Normalize virsh command field and reject blank values."""
        normalized = value.strip()
        if not normalized:
            msg = "vms_command must not be empty"
            raise ValueError(msg)
        return normalized

    @field_validator("radarr_interval", "sonarr_interval")
    @classmethod
    def _validate_servarr_interval(cls, v: float) -> float:
        """Ensure Servarr polling intervals are positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"servarr interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("radarr_timeout", "sonarr_timeout")
    @classmethod
    def _validate_servarr_timeout(cls, v: float) -> float:
        """Ensure Servarr timeouts are positive values."""
        if v <= 0.0:
            msg = f"servarr timeout must be > 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("radarr_calendar_days", "sonarr_calendar_days")
    @classmethod
    def _validate_servarr_calendar_days(cls, v: int) -> int:
        """Ensure Servarr calendar look-ahead remains bounded."""
        if not (1 <= v <= 31):
            msg = f"servarr calendar_days must be between 1 and 31, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("pihole_timeout")
    @classmethod
    def _validate_pihole_timeout(cls, v: float) -> float:
        """Ensure Pi-hole timeout is a positive value."""
        if v <= 0.0:
            msg = f"pihole_timeout must be > 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("pihole_interval")
    @classmethod
    def _validate_pihole_interval(cls, v: float) -> float:
        """Ensure Pi-hole polling interval is positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"pihole_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("synology_interval")
    @classmethod
    def _validate_synology_interval(cls, v: float) -> float:
        """Ensure Synology polling interval is positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"synology_interval must be between 1 and 3600 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("synology_timeout")
    @classmethod
    def _validate_synology_timeout(cls, v: float) -> float:
        """Ensure Synology timeout is positive and practical."""
        if not (0.5 <= v <= 120.0):
            msg = f"synology_timeout must be between 0.5 and 120 seconds, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("plex_timeout")
    @classmethod
    def _validate_plex_timeout(cls, v: float) -> float:
        """Ensure Plex timeout is a positive value."""
        if v <= 0.0:
            msg = f"plex_timeout must be > 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("plex_interval")
    @classmethod
    def _validate_plex_interval(cls, v: float) -> float:
        """Ensure Plex polling interval is positive and practical."""
        if not (1.0 <= v <= 3600.0):
            msg = f"plex_interval must be between 1 and 3600 seconds, got {v}"
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


def _get_yaml_bool(yaml_data: dict[str, object], key: str, default: bool) -> bool:
    """Read a bool value from YAML with string-safe normalization.

    Args:
        yaml_data: Parsed YAML mapping.
        key: Top-level YAML key.
        default: Value used when key is absent.

    Returns:
        Parsed boolean value.
    """
    raw = yaml_data.get(key, default)
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    return text not in {"0", "false", "no", "off", ""}


def get_config_path() -> Path:
    """Return the active config path from env or default.

    Returns:
        Absolute or relative path to the active ``casedd.yaml`` file.
    """
    return Path(os.environ.get("CASEDD_CONFIG", "casedd.yaml"))


def _rotation_entries_to_yaml(entries: list[RotationEntry]) -> list[object]:
    """Serialize rotation entries to YAML-friendly values.

    Args:
        entries: Rotation entries from runtime state.

    Returns:
        List containing either plain template names or mapping objects.
    """
    out: list[object] = []
    for entry in entries:
        if entry.seconds is None and not entry.skip_if:
            out.append(entry.template)
            continue
        out.append(entry.model_dump(mode="json", exclude_none=True))
    return out


def save_rotation_config_to_yaml(
    panel_name: str,
    rotation_templates: list[str],
    rotation_interval: float,
    rotation_enabled: bool,
    rotation_entries: list[RotationEntry] | None,
) -> Path:
    """Persist rotation settings to ``casedd.yaml``.

    This makes YAML the single source of truth for rotation settings used by
    startup and the advanced UI.

    Args:
        panel_name: Stable panel name to update.
        rotation_templates: Ordered template names when no per-entry payload
            is provided.
        rotation_interval: Default dwell time in seconds.
        rotation_enabled: Whether rotation is enabled.
        rotation_entries: Optional per-entry records including seconds/skip_if.

    Returns:
        Path to the YAML file that was updated.

    Raises:
        ValueError: If ``panel_name`` is unknown in a multi-panel config.
        OSError: If the file cannot be written.
    """
    config_path = get_config_path()
    yaml_data = _read_yaml(config_path)
    serialized_rotation = (
        _rotation_entries_to_yaml(rotation_entries)
        if rotation_entries is not None
        else [name for name in rotation_templates if name.strip()]
    )

    panels_raw = yaml_data.get("panels")
    if isinstance(panels_raw, list) and panels_raw:
        target_panel: dict[str, object] | None = None
        for panel_raw in panels_raw:
            if not isinstance(panel_raw, dict):
                continue
            if str(panel_raw.get("name", "")).strip() != panel_name:
                continue
            target_panel = panel_raw
            break
        if target_panel is None:
            msg = f"panel '{panel_name}' not found in YAML panels"
            raise ValueError(msg)
        target_panel["template_rotation"] = serialized_rotation
        target_panel["template_rotation_interval"] = float(rotation_interval)
        target_panel["template_rotation_enabled"] = bool(rotation_enabled)
    else:
        if panel_name != "primary":
            msg = (
                f"panel '{panel_name}' not found; single-panel YAML only supports "
                "panel 'primary'"
            )
            raise ValueError(msg)
        yaml_data["template_rotation"] = serialized_rotation
        yaml_data["template_rotation_interval"] = float(rotation_interval)
        yaml_data["template_rotation_enabled"] = bool(rotation_enabled)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(yaml_data, sort_keys=False)
    config_path.write_text(yaml_text, encoding="utf-8")
    return config_path


def _get_merged_value(
    yaml_data: dict[str, object],
    env_key: str,
    yaml_key: str,
    default: object,
) -> object:
    """Return merged config value with env var precedence over YAML/default."""
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    return yaml_data.get(yaml_key, default)


def _get_optional_float_merged(
    yaml_data: dict[str, object],
    env_key: str,
    yaml_key: str,
) -> float | None:
    """Parse an optional float from merged env/yaml values."""
    raw = str(_get_merged_value(yaml_data, env_key, yaml_key, "")).strip()
    if not raw:
        return None
    return float(raw)


def _get_int_with_blank_default_merged(
    yaml_data: dict[str, object],
    env_key: str,
    yaml_key: str,
    default: int,
) -> int:
    """Parse an int from merged env/yaml values, defaulting on blanks."""
    raw = str(_get_merged_value(yaml_data, env_key, yaml_key, default)).strip()
    if not raw:
        return default
    return int(raw)


def _get_rotation_templates_from_yaml(yaml_data: dict[str, object]) -> list[str | RotationEntry]:
    """Parse template rotation entries from YAML."""
    raw = yaml_data.get("template_rotation", [])
    if isinstance(raw, list):
        out: list[str | RotationEntry] = []
        for item in raw:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    out.append(name)
                continue
            if isinstance(item, RotationEntry):
                out.append(item)
                continue
            if isinstance(item, dict):
                out.append(RotationEntry.model_validate(item))
                continue
            name = str(item).strip()
            if name:
                out.append(name)
        return out
    text = str(raw)
    return [item.strip() for item in text.split(",") if item.strip()]


def _get_yaml_list_from_data(yaml_data: dict[str, object], key: str) -> list[object]:
    """Read a top-level YAML list with safe fallback."""
    raw = yaml_data.get(key, [])
    if isinstance(raw, list):
        return raw
    return []


def _get_always_collect_prefixes_from_data(yaml_data: dict[str, object]) -> list[str]:
    """Parse always-on getter prefixes from merged env/yaml values."""
    raw = _get_merged_value(
        yaml_data,
        "CASEDD_ALWAYS_COLLECT_PREFIXES",
        "always_collect_prefixes",
        [],
    )
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw)
    return [item.strip() for item in text.split(",") if item.strip()]


def _get_csv_or_list_from_merged(
    yaml_data: dict[str, object],
    env_key: str,
    yaml_key: str,
) -> list[str]:
    """Parse comma-delimited strings or YAML lists from merged config."""
    raw = _get_merged_value(yaml_data, env_key, yaml_key, [])
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw)
    return [item.strip() for item in text.split(",") if item.strip()]


def _get_tuya_devices_from_merged(yaml_data: dict[str, object]) -> list[TuyaDeviceConfig]:
    """Parse Tuya device definitions from merged env/yaml values."""
    env_raw = os.environ.get("CASEDD_TUYA_DEVICES")
    raw: object
    if env_raw is not None and env_raw.strip():
        parsed = yaml.safe_load(env_raw)
        raw = parsed if parsed is not None else []
    else:
        raw = yaml_data.get("tuya_devices", [])
    if not isinstance(raw, list):
        return []
    out: list[TuyaDeviceConfig] = []
    for item in raw:
        if isinstance(item, TuyaDeviceConfig):
            out.append(item)
            continue
        if isinstance(item, dict):
            out.append(TuyaDeviceConfig.model_validate(item))
    return out


def load_config() -> Config:
    """Build and return the active daemon configuration.

    Merges YAML file settings and environment variable overrides, with env
    vars taking highest priority. Rotation settings (``template_rotation`` and
    ``template_rotation_interval``) are intentionally YAML-only so the advanced
    UI and startup both use a single source of truth.

    Returns:
        A frozen :class:`Config` instance with all settings resolved.
    """
    # Determine config file path from env (before we've built Config)
    config_path = get_config_path()
    yaml_data = _read_yaml(config_path)

    # Keep thin wrappers local so existing call sites remain readable.
    def _get(env_key: str, yaml_key: str, default: object) -> object:
        return _get_merged_value(yaml_data, env_key, yaml_key, default)

    def _get_optional_float(env_key: str, yaml_key: str) -> float | None:
        return _get_optional_float_merged(yaml_data, env_key, yaml_key)

    def _get_int_with_blank_default(env_key: str, yaml_key: str, default: int) -> int:
        return _get_int_with_blank_default_merged(yaml_data, env_key, yaml_key, default)

    def _get_rotation_templates() -> list[str | RotationEntry]:
        return _get_rotation_templates_from_yaml(yaml_data)

    def _get_yaml_list(key: str) -> list[object]:
        return _get_yaml_list_from_data(yaml_data, key)

    def _get_always_collect_prefixes() -> list[str]:
        return _get_always_collect_prefixes_from_data(yaml_data)

    def _get_csv_or_list(env_key: str, yaml_key: str) -> list[str]:
        return _get_csv_or_list_from_merged(yaml_data, env_key, yaml_key)

    def _get_tuya_devices() -> list[TuyaDeviceConfig]:
        return _get_tuya_devices_from_merged(yaml_data)

    return Config(
        log_level=str(_get("CASEDD_LOG_LEVEL", "log_level", "INFO")),
        debug_frame_logs=str(
            _get("CASEDD_DEBUG_FRAME_LOGS", "debug_frame_logs", "0")
        ) not in {"0", "false", "False", ""},
        debug_perf_metrics=str(
            _get("CASEDD_DEBUG_PERF_METRICS", "debug_perf_metrics", "0")
        ) not in {"0", "false", "False", ""},
        no_fb=str(_get("CASEDD_NO_FB", "no_fb", "0")) not in {"0", "false", "False", ""},
        fb_device=Path(str(_get("CASEDD_FB_DEVICE", "fb_device", "/dev/fb1"))),
        fb_auto_detect=str(
            _get("CASEDD_FB_AUTO_DETECT", "fb_auto_detect", "0")
        ) not in {"0", "false", "False", ""},
        fb_rotation=_get_int_with_blank_default("CASEDD_FB_ROTATION", "fb_rotation", 0),
        fb_claim_on_no_input=str(
            _get("CASEDD_FB_CLAIM_ON_NO_INPUT", "fb_claim_on_no_input", "0")
        ) not in {"0", "false", "False", ""},
        ws_port=int(str(_get("CASEDD_WS_PORT", "ws_port", 8765))),
        http_port=int(str(_get("CASEDD_HTTP_PORT", "http_port", 8080))),
        socket_path=Path(
            str(_get("CASEDD_SOCKET_PATH", "socket_path", "/run/casedd/casedd.sock"))
        ),
        template=str(_get("CASEDD_TEMPLATE", "template", "system_stats")),
        startup_frame_seconds=float(
            str(_get("CASEDD_STARTUP_FRAME_SECONDS", "startup_frame_seconds", 5.0))
        ),
        refresh_rate=float(str(_get("CASEDD_REFRESH_RATE", "refresh_rate", 2.0))),
        width=_get_int_with_blank_default("CASEDD_WIDTH", "width", 800),
        height=_get_int_with_blank_default("CASEDD_HEIGHT", "height", 480),
        templates_dir=Path(str(_get("CASEDD_TEMPLATES_DIR", "templates_dir", "templates"))),
        assets_dir=Path(str(_get("CASEDD_ASSETS_DIR", "assets_dir", "assets"))),
        procfs_path=str(_get("CASEDD_PROCFS_PATH", "procfs_path", "/proc")),
        disk_mount=str(_get("CASEDD_DISK_MOUNT", "disk_mount", "/")),
        viewer_bg=str(_get("CASEDD_VIEWER_BG", "viewer_bg", "#0d0f12")),
        cpu_interval=float(str(_get("CASEDD_CPU_INTERVAL", "cpu_interval", 2.0))),
        gpu_interval=float(str(_get("CASEDD_GPU_INTERVAL", "gpu_interval", 5.0))),
        memory_interval=float(str(_get("CASEDD_MEMORY_INTERVAL", "memory_interval", 2.0))),
        disk_interval=float(str(_get("CASEDD_DISK_INTERVAL", "disk_interval", 2.0))),
        network_interval=float(str(_get("CASEDD_NETWORK_INTERVAL", "network_interval", 2.0))),
        system_interval=float(str(_get("CASEDD_SYSTEM_INTERVAL", "system_interval", 10.0))),
        fans_interval=float(str(_get("CASEDD_FANS_INTERVAL", "fans_interval", 3.0))),
        net_ports_interval=float(
            str(_get("CASEDD_NET_PORTS_INTERVAL", "net_ports_interval", 5.0))
        ),
        sysinfo_interval=float(str(_get("CASEDD_SYSINFO_INTERVAL", "sysinfo_interval", 30.0))),
        containers_interval=float(
            str(_get("CASEDD_CONTAINERS_INTERVAL", "containers_interval", 8.0))
        ),
        containers_runtime=str(
            _get("CASEDD_CONTAINERS_RUNTIME", "containers_runtime", "auto")
        ),
        containers_max_items=int(
            str(_get("CASEDD_CONTAINERS_MAX_ITEMS", "containers_max_items", 12))
        ),
        speedtest_interval=float(
            str(_get("CASEDD_SPEEDTEST_INTERVAL", "speedtest_interval", 1800.0))
        ),
        speedtest_startup_delay=float(
            str(_get("CASEDD_SPEEDTEST_STARTUP_DELAY", "speedtest_startup_delay", 0.0))
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
        speedtest_passive=str(
            _get("CASEDD_SPEEDTEST_PASSIVE", "speedtest_passive", "0")
        ) not in {"0", "false", "False", ""},
        speedtest_cache_path=Path(
            str(
                _get(
                    "CASEDD_SPEEDTEST_CACHE_PATH",
                    "speedtest_cache_path",
                    "run/speedtest-cache.json",
                )
            )
        ),
        speedtest_cache_max_age_hours=float(
            str(
                _get(
                    "CASEDD_SPEEDTEST_CACHE_MAX_AGE_HOURS",
                    "speedtest_cache_max_age_hours",
                    8.0,
                )
            )
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
        os_updates_interval=float(
            str(_get("CASEDD_OS_UPDATES_INTERVAL", "os_updates_interval", 900.0))
        ),
        os_updates_manager=str(
            _get("CASEDD_OS_UPDATES_MANAGER", "os_updates_manager", "auto")
        ),
        invokeai_base_url=str(
            _get("CASEDD_INVOKEAI_BASE_URL", "invokeai_base_url", "http://localhost:9090")
        ),
        invokeai_api_token=str(_get("CASEDD_INVOKEAI_API_TOKEN", "invokeai_api_token", "")).strip()
        or None,
        invokeai_interval=float(str(_get("CASEDD_INVOKEAI_INTERVAL", "invokeai_interval", 5.0))),
        invokeai_timeout=float(str(_get("CASEDD_INVOKEAI_TIMEOUT", "invokeai_timeout", 4.0))),
        invokeai_verify_tls=str(_get("CASEDD_INVOKEAI_VERIFY_TLS", "invokeai_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        ollama_api_base=str(_get("CASEDD_OLLAMA_API_BASE", "ollama_api_base", "http://localhost:11434")),
        ollama_interval=float(str(_get("CASEDD_OLLAMA_INTERVAL", "ollama_interval", 10.0))),
        ollama_timeout=float(str(_get("CASEDD_OLLAMA_TIMEOUT", "ollama_timeout", 3.0))),
        ollama_detailed=str(_get("CASEDD_OLLAMA_DETAILED", "ollama_detailed", "0"))
        not in {"0", "false", "False", ""},
        ollama_detail_max_models=int(
            str(_get("CASEDD_OLLAMA_DETAIL_MAX_MODELS", "ollama_detail_max_models", 8))
        ),
        ups_interval=float(str(_get("CASEDD_UPS_INTERVAL", "ups_interval", 5.0))),
        ups_command=str(_get("CASEDD_UPS_COMMAND", "ups_command", "")).strip() or None,
        ups_upsc_target=str(_get("CASEDD_UPS_UPSC_TARGET", "ups_upsc_target", "ups@localhost")),
        vms_interval=float(str(_get("CASEDD_VMS_INTERVAL", "vms_interval", 10.0))),
        vms_passive=str(_get("CASEDD_VMS_PASSIVE", "vms_passive", "0"))
        not in {"0", "false", "False", ""},
        vms_command=str(_get("CASEDD_VMS_COMMAND", "vms_command", "virsh")).strip()
        or "virsh",
        vms_max_items=int(str(_get("CASEDD_VMS_MAX_ITEMS", "vms_max_items", 8))),
        radarr_base_url=str(_get("CASEDD_RADARR_BASE_URL", "radarr_base_url", "")).strip(),
        radarr_api_key=str(_get("CASEDD_RADARR_API_KEY", "radarr_api_key", "")).strip()
        or None,
        radarr_interval=float(str(_get("CASEDD_RADARR_INTERVAL", "radarr_interval", 15.0))),
        radarr_timeout=float(str(_get("CASEDD_RADARR_TIMEOUT", "radarr_timeout", 4.0))),
        radarr_calendar_days=int(
            str(_get("CASEDD_RADARR_CALENDAR_DAYS", "radarr_calendar_days", 7))
        ),
        radarr_verify_tls=str(_get("CASEDD_RADARR_VERIFY_TLS", "radarr_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        sonarr_base_url=str(_get("CASEDD_SONARR_BASE_URL", "sonarr_base_url", "")).strip(),
        sonarr_api_key=str(_get("CASEDD_SONARR_API_KEY", "sonarr_api_key", "")).strip()
        or None,
        sonarr_interval=float(str(_get("CASEDD_SONARR_INTERVAL", "sonarr_interval", 15.0))),
        sonarr_timeout=float(str(_get("CASEDD_SONARR_TIMEOUT", "sonarr_timeout", 4.0))),
        sonarr_calendar_days=int(
            str(_get("CASEDD_SONARR_CALENDAR_DAYS", "sonarr_calendar_days", 7))
        ),
        sonarr_verify_tls=str(_get("CASEDD_SONARR_VERIFY_TLS", "sonarr_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        pihole_base_url=str(_get("CASEDD_PIHOLE_BASE_URL", "pihole_base_url", "http://pi.hole")),
        pihole_api_token=str(_get("CASEDD_PIHOLE_API_TOKEN", "pihole_api_token", "")).strip()
        or None,
        pihole_password=str(_get("CASEDD_PIHOLE_PASSWORD", "pihole_password", "")).strip()
        or None,
        pihole_session_sid=str(_get("CASEDD_PIHOLE_SESSION_SID", "pihole_session_sid", "")).strip()
        or None,
        pihole_timeout=float(str(_get("CASEDD_PIHOLE_TIMEOUT", "pihole_timeout", 4.0))),
        pihole_verify_tls=str(_get("CASEDD_PIHOLE_VERIFY_TLS", "pihole_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        pihole_interval=float(str(_get("CASEDD_PIHOLE_INTERVAL", "pihole_interval", 5.0))),
        synology_host=str(_get("CASEDD_SYNOLOGY_HOST", "synology_host", "")).strip(),
        synology_username=str(_get("CASEDD_SYNOLOGY_USERNAME", "synology_username", "")).strip()
        or None,
        synology_password=str(_get("CASEDD_SYNOLOGY_PASSWORD", "synology_password", "")).strip()
        or None,
        synology_sid=str(_get("CASEDD_SYNOLOGY_SID", "synology_sid", "")).strip() or None,
        synology_interval=float(str(_get("CASEDD_SYNOLOGY_INTERVAL", "synology_interval", 20.0))),
        synology_timeout=float(str(_get("CASEDD_SYNOLOGY_TIMEOUT", "synology_timeout", 5.0))),
        synology_verify_tls=str(_get("CASEDD_SYNOLOGY_VERIFY_TLS", "synology_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        synology_volume_exclude_regex=str(
            _get(
                "CASEDD_SYNOLOGY_VOLUME_EXCLUDE_REGEX",
                "synology_volume_exclude_regex",
                "",
            )
        ).strip()
        or None,
        synology_user_exclude_regex=str(
            _get(
                "CASEDD_SYNOLOGY_USER_EXCLUDE_REGEX",
                "synology_user_exclude_regex",
                "",
            )
        ).strip()
        or None,
        synology_surveillance_enabled=str(
            _get("CASEDD_SYNOLOGY_SURVEILLANCE_ENABLED", "synology_surveillance_enabled", "1")
        )
        not in {"0", "false", "False", ""},
        synology_surveillance_max_cameras=int(
            str(
                _get(
                    "CASEDD_SYNOLOGY_SURVEILLANCE_MAX_CAMERAS",
                    "synology_surveillance_max_cameras",
                    4,
                )
            )
        ),
        synology_camera_snapshot_enabled=str(
            _get(
                "CASEDD_SYNOLOGY_CAMERA_SNAPSHOT_ENABLED",
                "synology_camera_snapshot_enabled",
                "1",
            )
        )
        not in {"0", "false", "False", ""},
        synology_camera_snapshot_width=int(
            str(
                _get(
                    "CASEDD_SYNOLOGY_CAMERA_SNAPSHOT_WIDTH",
                    "synology_camera_snapshot_width",
                    640,
                )
            )
        ),
        synology_camera_snapshot_height=int(
            str(
                _get(
                    "CASEDD_SYNOLOGY_CAMERA_SNAPSHOT_HEIGHT",
                    "synology_camera_snapshot_height",
                    360,
                )
            )
        ),
        synology_camera_include_regex=str(
            _get(
                "CASEDD_SYNOLOGY_CAMERA_INCLUDE_REGEX",
                "synology_camera_include_regex",
                "",
            )
        ).strip()
        or None,
        synology_camera_exclude_regex=str(
            _get(
                "CASEDD_SYNOLOGY_CAMERA_EXCLUDE_REGEX",
                "synology_camera_exclude_regex",
                "",
            )
        ).strip()
        or None,
        synology_camera_exclude_statuses=str(
            _get(
                "CASEDD_SYNOLOGY_CAMERA_EXCLUDE_STATUSES",
                "synology_camera_exclude_statuses",
                "7",
            )
        ).strip()
        or "7",
        synology_dsm_updates_enabled=str(
            _get("CASEDD_SYNOLOGY_DSM_UPDATES_ENABLED", "synology_dsm_updates_enabled", "1")
        )
        not in {"0", "false", "False", ""},
        synology_strip_domain_hostname=str(
            _get(
                "CASEDD_SYNOLOGY_STRIP_DOMAIN_HOSTNAME",
                "synology_strip_domain_hostname",
                "1",
            )
        )
        not in {"0", "false", "False", ""},
        truenas_host=str(_get("CASEDD_TRUENAS_HOST", "truenas_host", "")).strip(),
        truenas_port=int(str(_get("CASEDD_TRUENAS_PORT", "truenas_port", 80))),
        truenas_api_key=str(_get("CASEDD_TRUENAS_API_KEY", "truenas_api_key", "")).strip()
        or None,
        truenas_interval=float(str(_get("CASEDD_TRUENAS_INTERVAL", "truenas_interval", 10.0))),
        truenas_timeout=float(str(_get("CASEDD_TRUENAS_TIMEOUT", "truenas_timeout", 5.0))),
        truenas_verify_tls=str(_get("CASEDD_TRUENAS_VERIFY_TLS", "truenas_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        truenas_strip_domain_hostname=str(
            _get(
                "CASEDD_TRUENAS_STRIP_DOMAIN_HOSTNAME",
                "truenas_strip_domain_hostname",
                "1",
            )
        )
        not in {"0", "false", "False", ""},
        tuya_devices=_get_tuya_devices(),
        tuya_interval=float(str(_get("CASEDD_TUYA_INTERVAL", "tuya_interval", 10.0))),
        tuya_cloud_enabled=str(
            _get("CASEDD_TUYA_CLOUD_ENABLED", "tuya_cloud_enabled", "0")
        ) not in {"0", "false", "False", ""},
        tuya_api_region=str(_get("CASEDD_TUYA_API_REGION", "tuya_api_region", "")).strip(),
        tuya_api_key=str(_get("CASEDD_TUYA_API_KEY", "tuya_api_key", "")).strip() or None,
        tuya_api_secret=str(_get("CASEDD_TUYA_API_SECRET", "tuya_api_secret", "")).strip()
        or None,
        tuya_api_device_id=str(
            _get("CASEDD_TUYA_API_DEVICE_ID", "tuya_api_device_id", "")
        ).strip()
        or None,
        plex_base_url=str(_get("CASEDD_PLEX_BASE_URL", "plex_base_url", "http://localhost:32400")),
        plex_token=str(_get("CASEDD_PLEX_TOKEN", "plex_token", "")).strip() or None,
        plex_client_identifier=str(
            _get("CASEDD_PLEX_CLIENT_IDENTIFIER", "plex_client_identifier", "casedd")
        ).strip()
        or "casedd",
        plex_product=str(_get("CASEDD_PLEX_PRODUCT", "plex_product", "CASEDD")).strip()
        or "CASEDD",
        plex_timeout=float(str(_get("CASEDD_PLEX_TIMEOUT", "plex_timeout", 4.0))),
        plex_verify_tls=str(_get("CASEDD_PLEX_VERIFY_TLS", "plex_verify_tls", "1"))
        not in {"0", "false", "False", ""},
        plex_interval=float(str(_get("CASEDD_PLEX_INTERVAL", "plex_interval", 5.0))),
        plex_max_sessions=int(str(_get("CASEDD_PLEX_MAX_SESSIONS", "plex_max_sessions", 6))),
        plex_max_recent=int(str(_get("CASEDD_PLEX_MAX_RECENT", "plex_max_recent", 6))),
        plex_privacy_filter_regex=str(
            _get("CASEDD_PLEX_PRIVACY_FILTER_REGEX", "plex_privacy_filter_regex", "")
        ).strip()
        or None,
        plex_privacy_filter_libraries=_get_csv_or_list(
            "CASEDD_PLEX_PRIVACY_FILTER_LIBRARIES",
            "plex_privacy_filter_libraries",
        ),
        plex_privacy_redaction_text=str(
            _get("CASEDD_PLEX_PRIVACY_REDACTION_TEXT", "plex_privacy_redaction_text", "[hidden]")
        ).strip()
        or "[hidden]",
        net_interfaces=_get_csv_or_list("CASEDD_NET_INTERFACES", "net_interfaces"),
        nzbget_url=str(_get("CASEDD_NZBGET_URL", "nzbget_url", "http://localhost:6789")),
        nzbget_username=str(_get("CASEDD_NZBGET_USERNAME", "nzbget_username", "")).strip() or None,
        nzbget_password=str(_get("CASEDD_NZBGET_PASSWORD", "nzbget_password", "")).strip() or None,
        nzbget_interval=float(str(_get("CASEDD_NZBGET_INTERVAL", "nzbget_interval", 5.0))),
        nzbget_timeout=float(str(_get("CASEDD_NZBGET_TIMEOUT", "nzbget_timeout", 3.0))),
        nzbget_category_filter_regex=str(
            _get(
                "CASEDD_NZBGET_CATEGORY_FILTER_REGEX",
                "nzbget_category_filter_regex",
                "",
            )
        ).strip() or None,
        sabnzbd_base_url=str(
            _get("CASEDD_SABNZBD_BASE_URL", "sabnzbd_base_url", "")
        ).strip(),
        sabnzbd_api_key=str(
            _get("CASEDD_SABNZBD_API_KEY", "sabnzbd_api_key", "")
        ).strip() or None,
        sabnzbd_interval=float(str(_get("CASEDD_SABNZBD_INTERVAL", "sabnzbd_interval", 5.0))),
        sabnzbd_timeout=float(str(_get("CASEDD_SABNZBD_TIMEOUT", "sabnzbd_timeout", 4.0))),
        sabnzbd_verify_tls=str(
            _get("CASEDD_SABNZBD_VERIFY_TLS", "sabnzbd_verify_tls", "true")
        ) not in {"0", "false", "False", ""},
        sabnzbd_max_slots=int(str(_get("CASEDD_SABNZBD_MAX_SLOTS", "sabnzbd_max_slots", 6))),
        jellyfin_base_url=str(
            _get("CASEDD_JELLYFIN_BASE_URL", "jellyfin_base_url", "")
        ).strip(),
        jellyfin_api_key=str(
            _get("CASEDD_JELLYFIN_API_KEY", "jellyfin_api_key", "")
        ).strip() or None,
        jellyfin_interval=float(
            str(_get("CASEDD_JELLYFIN_INTERVAL", "jellyfin_interval", 5.0))
        ),
        jellyfin_timeout=float(str(_get("CASEDD_JELLYFIN_TIMEOUT", "jellyfin_timeout", 4.0))),
        jellyfin_verify_tls=str(
            _get("CASEDD_JELLYFIN_VERIFY_TLS", "jellyfin_verify_tls", "true")
        ) not in {"0", "false", "False", ""},
        jellyfin_max_sessions=int(
            str(_get("CASEDD_JELLYFIN_MAX_SESSIONS", "jellyfin_max_sessions", 6))
        ),
        sports_enabled=str(
            _get("CASEDD_SPORTS_ENABLED", "sports_enabled", "false")
        ) not in {"0", "false", "False", ""},
        sports_api_key=str(
            _get("CASEDD_SPORTS_API_KEY", "sports_api_key", "123")
        ).strip() or "123",
        sports_interval=float(
            str(_get("CASEDD_SPORTS_INTERVAL", "sports_interval", 300.0))
        ),
        sports_timeout=float(str(_get("CASEDD_SPORTS_TIMEOUT", "sports_timeout", 5.0))),
        sports_max_teams=int(str(_get("CASEDD_SPORTS_MAX_TEAMS", "sports_max_teams", 10))),
        sports_recent_window_hours=int(
            str(_get("CASEDD_SPORTS_RECENT_WINDOW_HOURS", "sports_recent_window_hours", 48))
        ),
        sports_followed_teams=cast(
            "list[SportsTeamConfig]",
            _get_yaml_list("sports_followed_teams"),
        ),
        nasa_api_key=str(_get("CASEDD_NASA_API_KEY", "nasa_api_key", "")).strip() or None,
        apod_interval=float(str(_get("CASEDD_APOD_INTERVAL", "apod_interval", 3600.0))),
        apod_cache_dir=str(_get("CASEDD_APOD_CACHE_DIR", "apod_cache_dir", "/tmp/casedd-apod")),  # noqa: S108
        pushover_webhook_url=str(
            _get("CASEDD_PUSHOVER_WEBHOOK_URL", "pushover_webhook_url", "")
        ).strip() or None,
        template_rotation=_get_rotation_templates(),
        template_rotation_interval=float(str(yaml_data.get("template_rotation_interval", 30.0))),
        template_rotation_enabled=_get_yaml_bool(yaml_data, "template_rotation_enabled", True),
        template_schedule=cast(
            "list[TemplateScheduleRule]",
            _get_yaml_list("template_schedule"),
        ),
        template_triggers=cast(
            "list[TemplateTriggerRule]",
            _get_yaml_list("template_triggers"),
        ),
        trigger_border_color=str(
            _get("CASEDD_TRIGGER_BORDER_COLOR", "trigger_border_color", "#dc1e1e")
        ),
        panels=cast("list[PanelConfig]", _get_yaml_list("panels")),
        viewer_layout=ViewerLayout.model_validate(yaml_data["viewer_layout"])
        if isinstance(yaml_data.get("viewer_layout"), dict)
        else None,
        outputs=cast(
            "dict[str, OutputBackendConfig]",
            yaml_data.get("outputs", {}),
        ),
        always_collect_prefixes=_get_always_collect_prefixes(),
        test_mode=str(_get("CASEDD_TEST_MODE", "test_mode", "0"))
        not in {"0", "false", "False", ""},
        api_key=str(_get("CASEDD_API_KEY", "api_key", "")).strip() or None,
        api_basic_user=str(
            _get("CASEDD_API_BASIC_USER", "api_basic_user", "")
        ).strip()
        or None,
        api_basic_password=str(
            _get("CASEDD_API_BASIC_PASSWORD", "api_basic_password", "")
        ).strip()
        or None,
        api_rate_limit=int(str(_get("CASEDD_API_RATE_LIMIT", "api_rate_limit", 0))),
    )
