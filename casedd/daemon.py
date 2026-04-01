"""Main async orchestrator: starts and coordinates all CASEDD subsystems.

The :class:`Daemon` class is the central coordinator. It owns and manages:

- Data getters and getter scheduling
- Template registries and selector policies per panel
- Rendering engines and framebuffer outputs per panel
- WebSocket and HTTP viewer outputs
- Unix socket ingestion

Public API:
    - :class:`Daemon` — top-level async coordinator
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import signal
import time

from PIL import Image, ImageDraw
import psutil

from casedd.config import (
    Config,
    PanelConfig,
    RotationEntry,
    TemplateScheduleRule,
    TemplateTriggerRule,
    save_rotation_config_to_yaml,
)
from casedd.data_store import DataStore, StoreValue
from casedd.getter_health import GetterHealthRegistry
from casedd.getters.apod import ApodGetter
from casedd.getters.base import BaseGetter
from casedd.getters.cpu import CpuGetter
from casedd.getters.disk import DiskGetter
from casedd.getters.fans import FanGetter
from casedd.getters.gpu import GpuGetter
from casedd.getters.htop import HtopGetter
from casedd.getters.invokeai import InvokeAIGetter
from casedd.getters.memory import MemoryGetter
from casedd.getters.net_ports import NetPortsGetter
from casedd.getters.network import NetworkGetter
from casedd.getters.nzbget import NZBGetGetter
from casedd.getters.ollama import OllamaDetailOptions, OllamaGetter
from casedd.getters.pihole import PiHoleGetter
from casedd.getters.plex import PlexGetter
from casedd.getters.servarr import RadarrGetter, ServarrAggregateGetter, SonarrGetter
from casedd.getters.speedtest import SpeedtestGetter
from casedd.getters.sysinfo import SysinfoGetter
from casedd.getters.system import SystemGetter
from casedd.getters.ups import UpsGetter
from casedd.getters.weather import WeatherGetter
from casedd.ingestion.unix_socket import UnixSocketIngestion
from casedd.input_detect import has_local_keyboard_or_mouse
from casedd.notifications.pushover import send_pushover_webhook
from casedd.outputs.framebuffer import FramebufferOutput
from casedd.outputs.http_viewer import HttpViewerOutput
from casedd.outputs.websocket import WebSocketOutput
from casedd.renderer.color import parse_color
from casedd.renderer.engine import RenderEngine
from casedd.renderer.fonts import get_font
from casedd.speedtest_fields import enrich_speedtest_timestamp_fields
from casedd.template.models import Template, WidgetConfig
from casedd.template.registry import TemplateRegistry
from casedd.template.selector import TemplateSelector
from casedd.usb_display import (
    FramebufferInfo,
    find_framebuffers,
    find_usb_framebuffers,
    probe_framebuffer,
)

_log = logging.getLogger(__name__)

# Bind host for both WS and HTTP servers (all interfaces)
_BIND_HOST = "0.0.0.0"  # noqa: S104  # string compare, not bind
_GETTER_SYNC_INTERVAL_SEC = 5.0
_TEST_MODE_STORE_KEY = "casedd.test_mode"
_TEMPLATE_FORCE_PREFIX = "casedd.template.force."
_TEMPLATE_CURRENT_PREFIX = "casedd.template.current."
_SPEEDTEST_KEY_PREFIX = "speedtest."

# Visual indicator painted over trigger-held frames so the viewer knows
# the template is being forced by an out-of-spec condition.
_TRIGGER_BORDER_WIDTH: int = 6


def _draw_trigger_border(
    image: Image.Image,
    color: tuple[int, int, int],
) -> None:
    """Paint a colored border on *image* in-place to flag trigger-held frames.

    Args:
        image: The PIL RGB image to annotate.  Modified in place.
        color: Border color as an ``(r, g, b)`` tuple.
    """
    w, h = image.size
    draw = ImageDraw.Draw(image)
    for offset in range(_TRIGGER_BORDER_WIDTH):
        draw.rectangle(
            (offset, offset, w - 1 - offset, h - 1 - offset),
            outline=color,
        )


@dataclass
class _PanelRuntime:
    """Mutable runtime state for one panel output."""

    name: str
    display_name: str
    width: int
    height: int
    base_template: str
    rotation_templates: list[str]
    rotation_interval: float
    rotation_enabled: bool
    rotation_entries: list[RotationEntry]
    schedule_rules: list[TemplateScheduleRule]
    trigger_rules: list[TemplateTriggerRule]
    selector: TemplateSelector
    engine: RenderEngine
    framebuffer: FramebufferOutput
    fb_device: Path
    rotation: int
    current_template: str = ""


@dataclass
class _RenderLoopContext:
    """Input parameters for the render loop."""

    registry: TemplateRegistry
    panel_runtimes: list[_PanelRuntime]
    ws_output: WebSocketOutput
    http_output: HttpViewerOutput
    getters_by_name: dict[str, BaseGetter]
    getter_tasks: dict[str, asyncio.Task[None]]


class Daemon:
    """Top-level CASEDD coordinator."""

    def __init__(self, config: Config) -> None:
        """Initialise daemon internals.

        Args:
            config: Loaded daemon configuration.
        """
        self._cfg = config
        self._store = DataStore()
        self._shutdown = asyncio.Event()
        # Populated by USB display auto-detection when fb_auto_detect=True.
        self._auto_detected_fb: FramebufferInfo | None = None
        self._status_logo_path = Path(self._cfg.assets_dir) / "casedd-logo.png"
        self._health = GetterHealthRegistry()
        self._started_at: float = 0.0
        self._render_count: int = 0
        self._render_loop_last_ms: float = 0.0
        self._render_loop_ema_ms: float = 0.0
        self._render_loop_max_ms: float = 0.0

    def _record_render_timing(self, elapsed_ms: float) -> None:
        """Update rolling render-loop timing statistics."""
        self._render_loop_last_ms = elapsed_ms
        if self._render_loop_ema_ms <= 0.0:
            self._render_loop_ema_ms = elapsed_ms
        else:
            alpha = 0.2
            self._render_loop_ema_ms = (
                (1.0 - alpha) * self._render_loop_ema_ms
            ) + (alpha * elapsed_ms)
        self._render_loop_max_ms = max(self._render_loop_max_ms, elapsed_ms)

    async def run(self) -> None:
        """Start all subsystems and run the main render loop until shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown.set)
        # SIGHUP triggers a config and template hot-reload.
        loop.add_signal_handler(signal.SIGHUP, self._handle_sighup)
        self._started_at = time.time()

        self._store.set(_TEST_MODE_STORE_KEY, 1 if self._cfg.test_mode else 0)
        self._load_speedtest_cache()
        self._setup_framebuffer_detection()

        getters = self._create_getters()
        getters_by_name = {type(getter).__name__: getter for getter in getters}
        getter_tasks: dict[str, asyncio.Task[None]] = {}

        registry = TemplateRegistry(Path(self._cfg.templates_dir))
        await registry.start()

        panel_runtimes = self._build_panel_runtimes(registry)

        ws_output, http_output, unix_ingestion = self._setup_outputs_and_ingestion(
            panel_runtimes
        )

        await ws_output.start()
        await http_output.start()
        await unix_ingestion.start()

        active_templates = {
            panel.current_template
            for panel in panel_runtimes
            if panel.current_template
        }
        await self._sync_getter_tasks(
            registry,
            panel_runtimes,
            active_templates,
            getters_by_name,
            getter_tasks,
        )

        await self._show_startup_frame(panel_runtimes, ws_output, http_output)

        _log.info(
            "CASEDD daemon started. Panels: %d | Refresh: %.1f Hz",
            len(panel_runtimes),
            self._cfg.refresh_rate,
        )

        context = _RenderLoopContext(
            registry=registry,
            panel_runtimes=panel_runtimes,
            ws_output=ws_output,
            http_output=http_output,
            getters_by_name=getters_by_name,
            getter_tasks=getter_tasks,
        )

        try:
            await self._render_loop(context)
        finally:
            self._save_speedtest_cache()
            _log.info("Shutting down CASEDD daemon…")
            for getter in getters:
                getter.stop()
            for task in getter_tasks.values():
                task.cancel()
            await asyncio.gather(*getter_tasks.values(), return_exceptions=True)
            if panel_runtimes and ws_output is not None and http_output is not None:
                await self._show_shutdown_frame(panel_runtimes, ws_output, http_output)
            await unix_ingestion.stop()
            await ws_output.stop()
            await http_output.stop()
            await registry.stop()
            await self._blackout_framebuffers(panel_runtimes)
            _log.info("Daemon shutdown complete.")

    def _speedtest_snapshot(self) -> dict[str, StoreValue]:
        """Return only the ``speedtest.*`` namespace from the live store.

        Returns:
            Flat mapping of cached ``speedtest.*`` keys and primitive values.
        """
        snapshot = self._store.snapshot()
        return {
            key: value
            for key, value in snapshot.items()
            if key.startswith(_SPEEDTEST_KEY_PREFIX)
        }

    @staticmethod
    def _cache_payload_values(payload_obj: object) -> dict[str, StoreValue]:
        """Extract valid ``speedtest.*`` values from a decoded cache payload.

        Args:
            payload_obj: Decoded JSON object from the cache file.

        Returns:
            Sanitized ``speedtest.*`` mapping.
        """
        if not isinstance(payload_obj, dict):
            return {}

        out: dict[str, StoreValue] = {}
        for key_obj, value in payload_obj.items():
            if not isinstance(key_obj, str):
                continue
            if not key_obj.startswith(_SPEEDTEST_KEY_PREFIX):
                continue
            if isinstance(value, bool):
                continue
            if not isinstance(value, float | int | str):
                continue
            out[key_obj] = value
        return out

    def _load_speedtest_cache(self) -> None:
        """Restore fresh speedtest cache values into the in-memory data store."""
        cache_path = self._cfg.speedtest_cache_path
        try:
            raw_payload = cache_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            _log.warning("Could not read speedtest cache file: %s", cache_path)
            return

        try:
            payload_obj = json.loads(raw_payload)
        except json.JSONDecodeError:
            _log.warning("Ignoring invalid speedtest cache JSON: %s", cache_path)
            return

        if not isinstance(payload_obj, dict):
            _log.warning("Ignoring malformed speedtest cache payload: %s", cache_path)
            return

        values = self._cache_payload_values(payload_obj.get("data"))
        if not values:
            return

        saved_at_obj = payload_obj.get("saved_at_unix")
        if isinstance(saved_at_obj, bool):
            saved_at: float | None = None
        elif isinstance(saved_at_obj, int | float):
            saved_at = float(saved_at_obj)
        else:
            saved_at = None

        if saved_at is None:
            try:
                saved_at = cache_path.stat().st_mtime
            except OSError:
                saved_at = None

        if saved_at is not None:
            age_hours = (time.time() - saved_at) / 3600.0
            if age_hours > self._cfg.speedtest_cache_max_age_hours:
                _log.info(
                    "Skipping stale speedtest cache (age %.2fh > %.2fh)",
                    age_hours,
                    self._cfg.speedtest_cache_max_age_hours,
                )
                return

        enrich_speedtest_timestamp_fields(values)
        self._store.update(values)
        _log.info("Restored %d speedtest cache values from %s", len(values), cache_path)

    def _save_speedtest_cache(self) -> None:
        """Persist latest ``speedtest.*`` values for next daemon startup."""
        values = self._speedtest_snapshot()
        if not values:
            return

        cache_path = self._cfg.speedtest_cache_path
        payload = {
            "saved_at_unix": time.time(),
            "data": values,
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(payload, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            _log.warning("Could not write speedtest cache file: %s", cache_path)

    def _setup_framebuffer_detection(self) -> None:
        """Detect framebuffers and handle display claiming if configured."""
        detected_fbs = find_framebuffers()
        if detected_fbs:
            for fb_info in detected_fbs:
                _log.info("Detected framebuffer: %s", fb_info.describe())
        else:
            _log.warning("No framebuffer devices detected under /dev/fb*")

        if self._cfg.fb_auto_detect:
            usb_displays = find_usb_framebuffers()
            if usb_displays:
                self._auto_detected_fb = usb_displays[0]
                _log.info(
                    "Auto-detected USB framebuffer: %s",
                    self._auto_detected_fb.describe(),
                )
            else:
                _log.warning(
                    "fb_auto_detect enabled but no USB framebuffer displays found."
                )

        if self._cfg.fb_claim_on_no_input:
            try:
                if not has_local_keyboard_or_mouse():
                    keep_dir = Path("/run/casedd")
                    keep_dir.mkdir(parents=True, exist_ok=True)
                    (keep_dir / "keep-unblank").write_text("")
                    _log.info("No local input detected — claiming primary display")
                else:
                    _log.info("Local input detected — will not claim primary display")
            except Exception:
                _log.exception("Failed to evaluate/claim primary display")

    def _handle_sighup(self) -> None:
        """Handle SIGHUP by logging a hot-reload notice.

        Full config hot-reload requires a daemon restart because many
        subsystems (ports, device paths) cannot be changed at runtime.
        Template hot-reload is always active via the TemplateRegistry watcher.
        """
        _log.info(
            "SIGHUP received — templates reloading via TemplateRegistry watcher. "
            "Restart the daemon to apply config file changes."
        )

    def _setup_outputs_and_ingestion(
        self,
        panel_runtimes: list[_PanelRuntime],
    ) -> tuple[WebSocketOutput, HttpViewerOutput, UnixSocketIngestion]:
        """Create and configure WebSocket, HTTP, and Unix socket subsystems."""

        def _rotation_provider(panel_name: str) -> dict[str, object]:
            runtime = next((r for r in panel_runtimes if r.name == panel_name), None)
            if runtime is None:
                return {}
            return {
                "base_template": runtime.base_template,
                "rotation_templates": list(runtime.rotation_templates),
                "rotation_interval": runtime.rotation_interval,
                "rotation_enabled": runtime.rotation_enabled,
                "rotation_entries": [
                    e.model_dump(mode="json") for e in runtime.rotation_entries
                ],
            }

        def _rotation_updater(
            panel_name: str,
            templates: list[str],
            interval: float,
            enabled: bool,
            entries: list[RotationEntry] | None = None,
        ) -> None:
            runtime = next((r for r in panel_runtimes if r.name == panel_name), None)
            if runtime is None:
                return
            runtime.selector.update_rotation(templates, interval, enabled, entries)
            runtime.rotation_templates = (
                [entry.template for entry in entries]
                if entries is not None
                else templates
            )
            runtime.rotation_interval = interval
            runtime.rotation_enabled = enabled
            runtime.rotation_entries = list(runtime.selector.rotation_entries)
            try:
                save_rotation_config_to_yaml(
                    panel_name,
                    templates,
                    interval,
                    enabled,
                    entries,
                )
            except (OSError, ValueError):
                _log.warning(
                    "Could not persist rotation config to YAML for panel '%s'",
                    panel_name,
                )

        ws_output = WebSocketOutput(_BIND_HOST, self._cfg.ws_port)

        def _health_provider() -> dict[str, object]:
            """Return daemon health snapshot for /api/health and /api/metrics."""
            renderer_stats: dict[str, dict[str, object]] = {
                panel.name: panel.engine.latest_render_stats() for panel in panel_runtimes
            }
            dynamic_drawn = 0
            dynamic_cached = 0
            static_cache_hits = 0
            layout_cache_hits = 0
            for stats in renderer_stats.values():
                drawn_obj = stats.get("dynamic_drawn")
                if isinstance(drawn_obj, (int, float)):
                    dynamic_drawn += int(drawn_obj)

                cached_obj = stats.get("dynamic_cached")
                if isinstance(cached_obj, (int, float)):
                    dynamic_cached += int(cached_obj)

                static_cache_hits += int(bool(stats.get("static_cache_hit")))
                layout_cache_hits += int(bool(stats.get("layout_cache_hit")))

            return {
                "uptime_seconds": time.time() - self._started_at,
                "render_count": self._render_count,
                "render_loop_last_ms": self._render_loop_last_ms,
                "render_loop_ema_ms": self._render_loop_ema_ms,
                "render_loop_max_ms": self._render_loop_max_ms,
                "renderer_dynamic_drawn": dynamic_drawn,
                "renderer_dynamic_cached": dynamic_cached,
                "renderer_static_cache_hits": static_cache_hits,
                "renderer_layout_cache_hits": layout_cache_hits,
                "renderer": renderer_stats,
                "getters": self._health.snapshot(),
            }

        http_output = HttpViewerOutput(
            self._store,
            _BIND_HOST,
            self._cfg.http_port,
            self._cfg.ws_port,
            [
                {
                    "name": panel.name,
                    "display_name": panel.display_name,
                    "width": panel.width,
                    "height": panel.height,
                    "base_template": panel.base_template,
                    "rotation_templates": list(panel.rotation_templates),
                    "rotation_interval": panel.rotation_interval,
                    "rotation_enabled": panel.rotation_enabled,
                    "rotation_entries": [
                        e.model_dump(mode="json") for e in panel.rotation_entries
                    ],
                }
                for panel in panel_runtimes
            ],
            panel_runtimes[0].name,
            self._cfg.viewer_bg,
            Path(self._cfg.templates_dir),
            lambda: {
                panel.name: panel.engine.debug_state_snapshot() for panel in panel_runtimes
            },
            _rotation_provider,
            _rotation_updater,
            health_provider=_health_provider,
            api_key=self._cfg.api_key,
            api_basic_user=self._cfg.api_basic_user,
            api_basic_password=self._cfg.api_basic_password,
            api_rate_limit=self._cfg.api_rate_limit,
        )

        unix_ingestion = UnixSocketIngestion(Path(self._cfg.socket_path), self._store)

        return ws_output, http_output, unix_ingestion

    def _build_status_frame(
        self,
        width: int,
        height: int,
        title: str,
        lines: list[str],
    ) -> Image.Image:
        """Build a branded centered status frame.

        Args:
            width: Output width in pixels.
            height: Output height in pixels.
            title: Primary heading text.
            lines: Body lines to render below the title.

        Returns:
            PIL RGB image for display.
        """
        image = Image.new("RGB", (width, height), (10, 13, 16))
        draw = ImageDraw.Draw(image)

        # Subtle top-to-bottom wash for depth without adding complexity.
        for y in range(height):
            mix = y / max(1, height - 1)
            r = int(10 + (28 - 10) * mix)
            g = int(13 + (18 - 13) * mix)
            b = int(16 + (28 - 16) * mix)
            draw.line((0, y, width, y), fill=(r, g, b))

        margin = max(18, min(width, height) // 24)
        panel_radius = max(12, min(width, height) // 36)
        panel_box = (margin, margin, width - margin, height - margin)
        draw.rounded_rectangle(
            panel_box,
            radius=panel_radius,
            fill=(18, 23, 29),
            outline=(58, 70, 82),
            width=2,
        )

        accent_h = max(6, height // 120)
        draw.rounded_rectangle(
            (panel_box[0], panel_box[1], panel_box[2], panel_box[1] + accent_h + 6),
            radius=panel_radius,
            fill=(208, 96, 44),
        )

        logo_area_h = max(height // 3, 120)
        logo_max_w = max(width // 3, 140)
        logo_max_h = max(logo_area_h - margin, 120)
        logo_center_y = panel_box[1] + margin + logo_area_h // 2
        self._paste_status_logo(image, logo_max_w, logo_max_h, width // 2, logo_center_y)

        title_font = get_font(max(20, min(height // 10, 60)))
        body_font = get_font(max(12, min(height // 30, 26)))
        footer_font = get_font(max(10, min(height // 38, 20)))

        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_w = int(title_bbox[2] - title_bbox[0])
        title_h = int(title_bbox[3] - title_bbox[1])
        title_x = max(panel_box[0], (width - title_w) // 2)
        title_y = panel_box[1] + logo_area_h + max(12, height // 36)
        draw.text((title_x, title_y), title, fill=(240, 243, 247), font=title_font)

        line_y = title_y + title_h + max(14, height // 42)
        line_gap = max(6, height // 56)
        for line in lines:
            line_bbox = draw.textbbox((0, 0), line, font=body_font)
            line_w = int(line_bbox[2] - line_bbox[0])
            line_h = int(line_bbox[3] - line_bbox[1])
            line_x = max(panel_box[0] + margin, (width - line_w) // 2)
            draw.text((line_x, line_y), line, fill=(179, 188, 198), font=body_font)
            line_y += line_h + line_gap

        footer = "casedd"
        footer_bbox = draw.textbbox((0, 0), footer, font=footer_font)
        footer_w = int(footer_bbox[2] - footer_bbox[0])
        footer_h = int(footer_bbox[3] - footer_bbox[1])
        footer_x = width - margin - footer_w
        footer_y = height - margin - footer_h
        draw.text((footer_x, footer_y), footer, fill=(110, 122, 136), font=footer_font)

        return image

    def _paste_status_logo(
        self,
        image: Image.Image,
        max_width: int,
        max_height: int,
        center_x: int,
        center_y: int,
    ) -> None:
        """Paste the CASEDD logo scaled to fit within the target bounds."""
        if not self._status_logo_path.exists():
            return

        try:
            logo = Image.open(self._status_logo_path).convert("RGBA")
        except OSError:
            return

        src_w, src_h = logo.size
        if src_w <= 0 or src_h <= 0:
            return

        scale = min(max_width / src_w, max_height / src_h)
        if scale <= 0:
            return

        out_w = max(1, int(src_w * scale))
        out_h = max(1, int(src_h * scale))
        resized = logo.resize((out_w, out_h), Image.Resampling.LANCZOS)

        shadow = Image.new("RGBA", (out_w + 18, out_h + 18), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle(
            (8, 8, out_w + 10, out_h + 10),
            radius=max(12, out_h // 8),
            fill=(0, 0, 0, 80),
        )

        paste_x = center_x - out_w // 2
        paste_y = center_y - out_h // 2
        image.paste(shadow.convert("RGB"), (paste_x - 8, paste_y - 8), shadow)
        image.paste(resized.convert("RGB"), (paste_x, paste_y), resized)

    async def _display_panel_frame(
        self,
        panel: _PanelRuntime,
        image: Image.Image,
        ws_output: WebSocketOutput,
        http_output: HttpViewerOutput,
    ) -> None:
        """Write one image to framebuffer, HTTP viewer, and websocket clients."""
        await asyncio.to_thread(panel.framebuffer.write, image)
        http_output.set_latest_image(panel.name, image)
        await ws_output.broadcast(image, panel=panel.name)

    async def _show_startup_frame(
        self,
        panel_runtimes: list[_PanelRuntime],
        ws_output: WebSocketOutput,
        http_output: HttpViewerOutput,
    ) -> None:
        """Display a startup splash while getters warm up."""
        if self._cfg.startup_frame_seconds <= 0.0:
            return

        now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        for panel in panel_runtimes:
            lines = [
                f"Panel: {panel.display_name}",
                f"Framebuffer: {panel.fb_device}",
                f"Resolution: {panel.width}x{panel.height}",
                f"Template: {panel.base_template}",
                f"Refresh: {self._cfg.refresh_rate:.1f} Hz",
                f"Time: {now_str}",
                f"Waiting {self._cfg.startup_frame_seconds:.0f}s for initial data...",
            ]
            image = self._build_status_frame(panel.width, panel.height, "CASEDD starting", lines)
            await self._display_panel_frame(panel, image, ws_output, http_output)

        _log.info("Displaying startup frame for %.1f seconds", self._cfg.startup_frame_seconds)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                asyncio.shield(self._shutdown.wait()),
                timeout=self._cfg.startup_frame_seconds,
            )

    async def _show_shutdown_frame(
        self,
        panel_runtimes: list[_PanelRuntime],
        ws_output: WebSocketOutput,
        http_output: HttpViewerOutput,
    ) -> None:
        """Display a final shutdown splash before outputs are closed."""
        now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        _log.info("Displaying shutdown frame")
        for panel in panel_runtimes:
            lines = [
                f"Panel: {panel.display_name}",
                f"Framebuffer: {panel.fb_device}",
                f"Time: {now_str}",
                "Shutting down cleanly.",
            ]
            image = self._build_status_frame(panel.width, panel.height, "CASEDD stopping", lines)
            await self._display_panel_frame(panel, image, ws_output, http_output)

    async def _blackout_framebuffers(self, panel_runtimes: list[_PanelRuntime]) -> None:
        """Write a final black frame to all framebuffers before process exit."""
        _log.info("Writing final black frame to framebuffer(s)")
        for panel in panel_runtimes:
            black = Image.new("RGB", (panel.width, panel.height), (0, 0, 0))
            await asyncio.to_thread(panel.framebuffer.write, black)

    def _make_trigger_callback(
        self,
    ) -> Callable[[TemplateTriggerRule, StoreValue | None], None] | None:
        """Build a sync callback that fires Pushover webhook notifications.

        Returns ``None`` when no Pushover webhook URL is configured, so the
        selector skips the call entirely.

        Returns:
            A callback suitable for ``TemplateSelector.on_trigger_activate``,
            or ``None`` if notifications are not configured.
        """
        webhook_url = self._cfg.pushover_webhook_url
        if not webhook_url:
            return None

        def _notify(rule: TemplateTriggerRule, value: StoreValue | None) -> None:
            _task = asyncio.ensure_future(  # noqa: RUF006  # fire-and-forget; errors logged in coroutine
                send_pushover_webhook(webhook_url, rule, value)
            )

        return _notify

    def _resolve_rotation_config(
        self,
        panel: PanelConfig,
    ) -> tuple[list[str], list[RotationEntry] | None]:
        """Normalize panel rotation config into templates and optional entries.

        Supports mixed config lists containing plain template names and
        :class:`RotationEntry` records with per-template dwell times.

        Args:
            panel: Panel configuration to normalize.

        Returns:
            Tuple of ``(rotation_templates, rotation_entries_or_none)``.
        """
        if not panel.template_rotation:
            return [], None

        rotation_templates = [
            item.template if isinstance(item, RotationEntry) else item
            for item in panel.template_rotation
        ]
        if not any(isinstance(item, RotationEntry) for item in panel.template_rotation):
            return rotation_templates, None

        rotation_entries = [
            item if isinstance(item, RotationEntry) else RotationEntry(template=item)
            for item in panel.template_rotation
        ]
        return rotation_templates, rotation_entries

    def _build_panel_runtimes(self, registry: TemplateRegistry) -> list[_PanelRuntime]:
        """Create panel runtimes from config or legacy single-panel settings.

        Args:
            registry: Template registry used for initial template validation.

        Returns:
            List of panel runtime records.
        """
        panel_cfgs = self._cfg.panels or [
            PanelConfig(
                name="primary",
                display_name="Primary",
                fb_device=self._cfg.fb_device,
                no_fb=self._cfg.no_fb,
                # Pass cfg.width/height explicitly so the probe doesn't
                # override an intentional user setting (e.g. CASEDD_WIDTH=1024).
                # For multi-panel configs, panels that omit width/height keep
                # None and still benefit from framebuffer auto-probing.
                width=self._cfg.width,
                height=self._cfg.height,
                template=self._cfg.template,
                template_rotation=self._cfg.template_rotation,
                template_rotation_interval=self._cfg.template_rotation_interval,
                template_rotation_enabled=self._cfg.template_rotation_enabled,
                template_schedule=self._cfg.template_schedule,
                template_triggers=self._cfg.template_triggers,
            )
        ]

        runtimes: list[_PanelRuntime] = []
        for panel in panel_cfgs:
            panel_name = panel.name
            display_name = panel.display_name or panel.name
            width = panel.width if panel.width is not None else 0
            height = panel.height if panel.height is not None else 0
            base_template = panel.template if panel.template is not None else self._cfg.template
            rotation_templates, configured_rotation_entries = self._resolve_rotation_config(panel)
            schedule_rules = list(panel.template_schedule)
            trigger_rules = list(panel.template_triggers)
            rotation_interval = (
                panel.template_rotation_interval
                if panel.template_rotation_interval is not None
                else self._cfg.template_rotation_interval
            )
            rotation_enabled = (
                panel.template_rotation_enabled
                if panel.template_rotation_enabled is not None
                else self._cfg.template_rotation_enabled
            )
            rotation_entries: list[RotationEntry] | None = configured_rotation_entries

            force_key = f"{_TEMPLATE_FORCE_PREFIX}{panel_name}"
            selector = TemplateSelector(
                base_template=base_template,
                rotation_templates=rotation_templates,
                rotation_interval=rotation_interval,
                rotation_enabled=rotation_enabled,
                schedule_rules=schedule_rules,
                trigger_rules=trigger_rules,
                force_store_key=force_key,
                rotation_entries=rotation_entries,
                template_resolver=registry.get_template_skip_if,
                on_trigger_activate=self._make_trigger_callback(),
            )

            fb_device = panel.fb_device if panel.fb_device is not None else self._cfg.fb_device
            no_fb = panel.no_fb if panel.no_fb is not None else self._cfg.no_fb

            # USB auto-detect: select the target framebuffer device BEFORE probing
            # for resolution.  When fb_auto_detect is enabled and a USB display was
            # found, always prefer that display — even if the configured device exists
            # (it may be the GPU's framebuffer reporting an unusable 4K resolution).
            # Without auto-detect, fall back to the USB display only when the
            # configured device is absent.
            if self._auto_detected_fb is not None:
                if self._cfg.fb_auto_detect:
                    fb_device = self._auto_detected_fb.device
                    _log.info(
                        "Panel '%s': fb_auto_detect — using USB display %s",
                        panel_name, fb_device,
                    )
                elif not fb_device.exists():
                    fb_device = self._auto_detected_fb.device
                    _log.info(
                        "Panel '%s': configured framebuffer absent, "
                        "falling back to USB display %s",
                        panel_name, fb_device,
                    )

            # Probe the selected device for its native resolution so that the
            # render canvas matches the physical display without manual config.
            try:
                probe_info = probe_framebuffer(fb_device)
                if probe_info is not None:
                    if (panel.width is None) and probe_info.width > 0:
                        width = probe_info.width
                    if (panel.height is None) and probe_info.height > 0:
                        height = probe_info.height
            except Exception:
                _log.debug("Could not probe framebuffer %s for dimensions", fb_device)

            # Final fallback to configured defaults when resolution is still
            # unknown (e.g., device not yet connected, missing sysfs virtual_size).
            if width <= 0:
                width = self._cfg.width
            if height <= 0:
                height = self._cfg.height

            # Determine rotation: per-panel override wins, otherwise global
            rot = panel.rotation if panel.rotation is not None else self._cfg.fb_rotation
            framebuffer = FramebufferOutput(fb_device, disabled=no_fb, rotation=rot)

            # Validate template availability early so startup fails loudly if broken.
            registry.get(base_template)

            runtime = _PanelRuntime(
                name=panel_name,
                display_name=display_name,
                width=width,
                height=height,
                base_template=base_template,
                rotation_templates=rotation_templates,
                rotation_interval=rotation_interval,
                rotation_enabled=rotation_enabled,
                rotation_entries=list(selector.rotation_entries),
                schedule_rules=schedule_rules,
                trigger_rules=trigger_rules,
                selector=selector,
                engine=RenderEngine(
                    width,
                    height,
                    debug_frame_logs=self._cfg.debug_frame_logs,
                    display_padding=self._cfg.display_padding,
                ),
                framebuffer=framebuffer,
                fb_device=fb_device,
                rotation=rot,
            )
            _log.info(
                "Panel '%s' configured: fb=%s size=%dx%d rotation=%d",
                panel_name,
                fb_device,
                width,
                height,
                rot,
            )
            runtime.current_template = runtime.selector.select_template(self._store.snapshot())
            self._store.set(f"{_TEMPLATE_CURRENT_PREFIX}{panel_name}", runtime.current_template)
            runtimes.append(runtime)

        return runtimes

    async def _render_loop(self, context: _RenderLoopContext) -> None:
        """Drive render/output cycle at configured refresh rate."""
        interval = 1.0 / self._cfg.refresh_rate
        last_getter_sync = 0.0

        while not self._shutdown.is_set():
            tick_start = asyncio.get_event_loop().time()
            snapshot = self._store.snapshot()
            active_templates: set[str] = set()

            for panel in context.panel_runtimes:
                selected = panel.selector.select_template(snapshot)
                active_templates.add(selected)
                if selected != panel.current_template:
                    panel.current_template = selected
                    self._store.set(f"{_TEMPLATE_CURRENT_PREFIX}{panel.name}", selected)
                    _log.info("Panel '%s' switched template to '%s'", panel.name, selected)

                image = await asyncio.to_thread(
                    self._render_one,
                    context.registry,
                    panel.engine,
                    selected,
                )
                if image is None:
                    continue

                if panel.selector.is_trigger_held:
                    border_color = parse_color(self._cfg.trigger_border_color)
                    await asyncio.to_thread(_draw_trigger_border, image, border_color)

                await asyncio.to_thread(panel.framebuffer.write, image)
                context.http_output.set_latest_image(panel.name, image)
                await context.ws_output.broadcast(image, panel=panel.name)
                self._render_count += 1

            if tick_start - last_getter_sync >= _GETTER_SYNC_INTERVAL_SEC:
                await self._sync_getter_tasks(
                    context.registry,
                    context.panel_runtimes,
                    active_templates,
                    context.getters_by_name,
                    context.getter_tasks,
                )
                last_getter_sync = tick_start

            elapsed = asyncio.get_event_loop().time() - tick_start
            self._record_render_timing(elapsed * 1000.0)
            sleep_time = max(0.0, interval - elapsed)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._shutdown.wait()), timeout=sleep_time)

    def _render_one(
        self,
        registry: TemplateRegistry,
        engine: RenderEngine,
        template_name: str,
    ) -> Image.Image | None:
        """Render one image for a single panel/template."""
        try:
            template = registry.get(template_name)
        except Exception:
            _log.warning("Template '%s' not found — skipping frame.", template_name)
            return None

        try:
            return engine.render(template, self._store)
        except Exception:
            _log.exception("Frame render error — continuing.")
            return None

    def _create_getters(self) -> list[BaseGetter]:
        """Instantiate all getter objects with shared data store and health registry."""
        if hasattr(psutil, "PROCFS_PATH"):
            psutil.PROCFS_PATH = self._cfg.procfs_path

        getters: list[BaseGetter] = [
            CpuGetter(self._store),
            GpuGetter(self._store),
            MemoryGetter(self._store),
            DiskGetter(self._store, mount=self._cfg.disk_mount),
            NetworkGetter(self._store, interfaces=self._cfg.net_interfaces),
            SystemGetter(self._store),
            FanGetter(self._store),
            HtopGetter(
                self._store,
                interval=self._cfg.htop_interval,
                max_rows=self._cfg.htop_max_rows,
            ),
            SpeedtestGetter(
                self._store,
                interval=self._cfg.speedtest_interval,
                startup_delay=self._cfg.speedtest_startup_delay,
                passive=self._cfg.speedtest_passive,
                binary=self._cfg.speedtest_binary,
                server_id=self._cfg.speedtest_server_id,
                advertised_down_mbps=self._cfg.speedtest_advertised_down_mbps,
                advertised_up_mbps=self._cfg.speedtest_advertised_up_mbps,
                reference_down_mbps=self._cfg.speedtest_reference_down_mbps,
                reference_up_mbps=self._cfg.speedtest_reference_up_mbps,
                marginal_ratio=self._cfg.speedtest_marginal_ratio,
                critical_ratio=self._cfg.speedtest_critical_ratio,
            ),
            InvokeAIGetter(
                self._store,
                base_url=self._cfg.invokeai_base_url,
                api_token=self._cfg.invokeai_api_token,
                interval=self._cfg.invokeai_interval,
                timeout=self._cfg.invokeai_timeout,
                verify_tls=self._cfg.invokeai_verify_tls,
            ),
            OllamaGetter(
                self._store,
                base_url=self._cfg.ollama_api_base,
                interval=self._cfg.ollama_interval,
                timeout=self._cfg.ollama_timeout,
                detail=OllamaDetailOptions(
                    enabled=self._cfg.ollama_detailed,
                    max_models=self._cfg.ollama_detail_max_models,
                ),
            ),
            UpsGetter(
                self._store,
                interval=self._cfg.ups_interval,
                command=self._cfg.ups_command,
                upsc_target=self._cfg.ups_upsc_target,
            ),
            PiHoleGetter(
                self._store,
                base_url=self._cfg.pihole_base_url,
                api_token=self._cfg.pihole_api_token,
                password=self._cfg.pihole_password,
                session_sid=self._cfg.pihole_session_sid,
                interval=self._cfg.pihole_interval,
                timeout=self._cfg.pihole_timeout,
                verify_tls=self._cfg.pihole_verify_tls,
            ),
            RadarrGetter(
                self._store,
                base_url=self._cfg.radarr_base_url,
                api_key=self._cfg.radarr_api_key or "",
                interval=self._cfg.radarr_interval,
                timeout=self._cfg.radarr_timeout,
                calendar_days=self._cfg.radarr_calendar_days,
                verify_tls=self._cfg.radarr_verify_tls,
            ),
            SonarrGetter(
                self._store,
                base_url=self._cfg.sonarr_base_url,
                api_key=self._cfg.sonarr_api_key or "",
                interval=self._cfg.sonarr_interval,
                timeout=self._cfg.sonarr_timeout,
                calendar_days=self._cfg.sonarr_calendar_days,
                verify_tls=self._cfg.sonarr_verify_tls,
            ),
            ServarrAggregateGetter(
                self._store,
                interval=min(self._cfg.radarr_interval, self._cfg.sonarr_interval),
            ),
            PlexGetter(
                self._store,
                base_url=self._cfg.plex_base_url,
                token=self._cfg.plex_token,
                client_identifier=self._cfg.plex_client_identifier,
                product=self._cfg.plex_product,
                interval=self._cfg.plex_interval,
                timeout=self._cfg.plex_timeout,
                verify_tls=self._cfg.plex_verify_tls,
                max_sessions=self._cfg.plex_max_sessions,
                max_recent=self._cfg.plex_max_recent,
                privacy_filter_regex=self._cfg.plex_privacy_filter_regex,
                privacy_filter_libraries=self._cfg.plex_privacy_filter_libraries,
                privacy_redaction_text=self._cfg.plex_privacy_redaction_text,
            ),
            NZBGetGetter(
                self._store,
                url=self._cfg.nzbget_url,
                username=self._cfg.nzbget_username,
                password=self._cfg.nzbget_password,
                interval=self._cfg.nzbget_interval,
                timeout=self._cfg.nzbget_timeout,
                category_filter_regex=self._cfg.nzbget_category_filter_regex,
            ),
            WeatherGetter(
                self._store,
                provider=self._cfg.weather_provider,
                interval=self._cfg.weather_interval,
                zipcode=self._cfg.weather_zipcode,
                lat=self._cfg.weather_lat,
                lon=self._cfg.weather_lon,
                user_agent=self._cfg.weather_user_agent,
            ),
            ApodGetter(
                self._store,
                api_key=self._cfg.nasa_api_key,
                interval=self._cfg.apod_interval,
                cache_dir=self._cfg.apod_cache_dir,
            ),
            NetPortsGetter(self._store),
            SysinfoGetter(self._store),
        ]
        # Attach health registry so each getter reports outcomes.
        for getter in getters:
            getter.attach_health(self._health)
        return getters

    async def _sync_getter_tasks(
        self,
        registry: TemplateRegistry,
        panel_runtimes: list[_PanelRuntime],
        active_templates: set[str],
        getters_by_name: dict[str, BaseGetter],
        getter_tasks: dict[str, asyncio.Task[None]],
    ) -> None:
        """Start/stop getter tasks based on current policy and test mode."""
        needed: set[str]
        if self._is_test_mode_enabled(self._store.snapshot()):
            needed = set()
        else:
            needed = self._needed_getter_names(registry, panel_runtimes, active_templates)

        for name in list(getter_tasks.keys()):
            if name in needed:
                continue
            getter = getters_by_name[name]
            getter.stop()
            task = getter_tasks.pop(name)
            task.cancel()

        for name in sorted(needed):
            if name in getter_tasks:
                continue
            getter_opt = getters_by_name.get(name)
            if getter_opt is None:
                continue
            task = asyncio.create_task(getter_opt.run(), name=f"getter-{name}")
            getter_tasks[name] = task

    def _needed_getter_names(
        self,
        registry: TemplateRegistry,
        panel_runtimes: list[_PanelRuntime],
        active_templates: set[str],
    ) -> set[str]:
        """Resolve getter set required by active and potential panel templates."""
        names: set[str] = set()

        template_names = set(active_templates)
        for panel in panel_runtimes:
            template_names.add(panel.base_template)
            # Use rotation_entries when available (may differ from rotation_templates
            # when a persisted or entries-based rotation config is active).
            template_names.update(e.template for e in panel.rotation_entries)
            template_names.update(rule.template for rule in panel.schedule_rules)
            template_names.update(rule.template for rule in panel.trigger_rules)

            # The trigger condition source (e.g. "nvidia.percent") needs its
            # getter running even when that namespace isn't used by the target
            # template's widgets (e.g. a cpu.temperature trigger → apod template).
            for rule in panel.trigger_rules:
                getter_name = self._getter_name_for_source(rule.source)
                if getter_name is not None:
                    names.add(getter_name)

            forced = self._store.get(f"{_TEMPLATE_FORCE_PREFIX}{panel.name}")
            if isinstance(forced, str) and forced.strip() and forced.strip().lower() != "auto":
                template_names.add(forced.strip())

        for template_name in sorted(template_names):
            try:
                template = registry.get(template_name)
            except Exception:
                _log.debug(
                    "Skipping template '%s' while computing getter requirements",
                    template_name,
                )
                continue
            for source in self._template_sources(template):
                getter_name = self._getter_name_for_source(source)
                if getter_name is not None:
                    names.add(getter_name)

        for prefix in self._cfg.always_collect_prefixes:
            getter_name = self._getter_name_for_source(f"{prefix}.placeholder")
            if getter_name is not None:
                names.add(getter_name)

        return names

    @staticmethod
    def _getter_name_for_source(source: str) -> str | None:
        """Map source key namespace to getter class name."""
        mapping: tuple[tuple[str, str], ...] = (
            ("cpu.", "CpuGetter"),
            ("nvidia.", "GpuGetter"),
            ("memory.", "MemoryGetter"),
            ("disk.", "DiskGetter"),
            ("net.", "NetworkGetter"),
            ("system.", "SystemGetter"),
            ("fans.", "FanGetter"),
            ("speedtest.", "SpeedtestGetter"),
            ("invokeai.", "InvokeAIGetter"),
            ("ollama.", "OllamaGetter"),
            ("ups.", "UpsGetter"),
            ("pihole.", "PiHoleGetter"),
            ("radarr.", "RadarrGetter"),
            ("sonarr.", "SonarrGetter"),
            ("servarr.", "ServarrAggregateGetter"),
            ("htop.", "HtopGetter"),
            ("plex.", "PlexGetter"),
            ("nzbget.", "NZBGetGetter"),
            ("weather.", "WeatherGetter"),
            ("apod.", "ApodGetter"),
            ("netports.", "NetPortsGetter"),
            ("sysinfo.", "SysinfoGetter"),
        )
        for prefix, getter_name in mapping:
            if source.startswith(prefix):
                return getter_name
        return None

    def _template_sources(self, template: Template) -> set[str]:
        """Collect source keys referenced by a template tree."""
        sources: set[str] = set()
        for cfg in template.widgets.values():
            self._collect_widget_sources(cfg, sources)
        return sources

    def _collect_widget_sources(self, cfg: WidgetConfig, out: set[str]) -> None:
        """Recursively collect source keys from one widget config."""
        if cfg.source is not None:
            out.add(cfg.source)
        for source in cfg.sources:
            out.add(source)
        for child in cfg.children:
            self._collect_widget_sources(child, out)
        for child in cfg.children_named.values():
            self._collect_widget_sources(child, out)

    @staticmethod
    def _is_test_mode_enabled(snapshot: dict[str, StoreValue]) -> bool:
        """Resolve global test mode from data-store flags.

        Args:
            snapshot: Current data-store snapshot.

        Returns:
            ``True`` when getter polling should be disabled.
        """
        raw = snapshot.get(_TEST_MODE_STORE_KEY)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return raw != 0
        if isinstance(raw, str):
            return raw.strip().lower() not in {"", "0", "false", "no", "off"}
        return False
