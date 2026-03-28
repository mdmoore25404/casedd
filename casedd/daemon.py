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
import contextlib
from dataclasses import dataclass
import logging
from pathlib import Path
import signal

from PIL import Image
import psutil

from casedd.config import Config, PanelConfig, TemplateScheduleRule, TemplateTriggerRule
from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter
from casedd.getters.cpu import CpuGetter
from casedd.getters.disk import DiskGetter
from casedd.getters.fans import FanGetter
from casedd.getters.gpu import GpuGetter
from casedd.getters.memory import MemoryGetter
from casedd.getters.network import NetworkGetter
from casedd.getters.ollama import OllamaGetter
from casedd.getters.speedtest import SpeedtestGetter
from casedd.getters.system import SystemGetter
from casedd.getters.ups import UpsGetter
from casedd.ingestion.unix_socket import UnixSocketIngestion
from casedd.outputs.framebuffer import FramebufferOutput
from casedd.outputs.http_viewer import HttpViewerOutput
from casedd.outputs.websocket import WebSocketOutput
from casedd.renderer.engine import RenderEngine
from casedd.template.models import Template, WidgetConfig
from casedd.template.registry import TemplateRegistry
from casedd.template.selector import TemplateSelector

_log = logging.getLogger(__name__)

# Bind host for both WS and HTTP servers (all interfaces)
_BIND_HOST = "0.0.0.0"  # noqa: S104 — intentional; CASEDD is a local display server
_GETTER_SYNC_INTERVAL_SEC = 5.0
_TEST_MODE_STORE_KEY = "casedd.test_mode"
_TEMPLATE_FORCE_PREFIX = "casedd.template.force."
_TEMPLATE_CURRENT_PREFIX = "casedd.template.current."


@dataclass
class _PanelRuntime:
    """Mutable runtime state for one panel output."""

    name: str
    display_name: str
    width: int
    height: int
    base_template: str
    rotation_templates: list[str]
    schedule_rules: list[TemplateScheduleRule]
    trigger_rules: list[TemplateTriggerRule]
    selector: TemplateSelector
    engine: RenderEngine
    framebuffer: FramebufferOutput
    current_template: str = ""


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

    async def run(self) -> None:
        """Start all subsystems and run the main render loop until shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown.set)

        self._store.set(_TEST_MODE_STORE_KEY, 1 if self._cfg.test_mode else 0)

        getters = self._create_getters()
        getters_by_name = {type(getter).__name__: getter for getter in getters}
        getter_tasks: dict[str, asyncio.Task[None]] = {}

        registry = TemplateRegistry(Path(self._cfg.templates_dir))
        await registry.start()

        panel_runtimes = self._build_panel_runtimes(registry)

        ws_output = WebSocketOutput(_BIND_HOST, self._cfg.ws_port)
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
                }
                for panel in panel_runtimes
            ],
            panel_runtimes[0].name,
            self._cfg.viewer_bg,
            Path(self._cfg.templates_dir),
            lambda: {
                panel.name: panel.engine.debug_state_snapshot() for panel in panel_runtimes
            },
        )

        unix_ingestion = UnixSocketIngestion(Path(self._cfg.socket_path), self._store)

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

        _log.info(
            "CASEDD daemon started. Panels: %d | Refresh: %.1f Hz",
            len(panel_runtimes),
            self._cfg.refresh_rate,
        )

        try:
            await self._render_loop(
                registry,
                panel_runtimes,
                ws_output,
                http_output,
                getters_by_name,
                getter_tasks,
            )
        finally:
            _log.info("Shutting down CASEDD daemon…")
            for getter in getters:
                getter.stop()
            for task in getter_tasks.values():
                task.cancel()
            await asyncio.gather(*getter_tasks.values(), return_exceptions=True)
            await unix_ingestion.stop()
            await ws_output.stop()
            await http_output.stop()
            await registry.stop()
            _log.info("Daemon shutdown complete.")

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
                width=self._cfg.width,
                height=self._cfg.height,
                template=self._cfg.template,
                template_rotation=self._cfg.template_rotation,
                template_rotation_interval=self._cfg.template_rotation_interval,
                template_schedule=self._cfg.template_schedule,
                template_triggers=self._cfg.template_triggers,
            )
        ]

        runtimes: list[_PanelRuntime] = []
        for panel in panel_cfgs:
            panel_name = panel.name
            display_name = panel.display_name or panel.name
            width = panel.width if panel.width is not None else self._cfg.width
            height = panel.height if panel.height is not None else self._cfg.height
            base_template = panel.template if panel.template is not None else self._cfg.template
            rotation_templates = list(panel.template_rotation)
            schedule_rules = list(panel.template_schedule)
            trigger_rules = list(panel.template_triggers)
            rotation_interval = (
                panel.template_rotation_interval
                if panel.template_rotation_interval is not None
                else self._cfg.template_rotation_interval
            )

            force_key = f"{_TEMPLATE_FORCE_PREFIX}{panel_name}"
            selector = TemplateSelector(
                base_template=base_template,
                rotation_templates=rotation_templates,
                rotation_interval=rotation_interval,
                schedule_rules=schedule_rules,
                trigger_rules=trigger_rules,
                force_store_key=force_key,
            )

            fb_device = panel.fb_device if panel.fb_device is not None else self._cfg.fb_device
            no_fb = panel.no_fb if panel.no_fb is not None else self._cfg.no_fb
            framebuffer = FramebufferOutput(fb_device, disabled=no_fb)

            # Validate template availability early so startup fails loudly if broken.
            registry.get(base_template)

            runtime = _PanelRuntime(
                name=panel_name,
                display_name=display_name,
                width=width,
                height=height,
                base_template=base_template,
                rotation_templates=rotation_templates,
                schedule_rules=schedule_rules,
                trigger_rules=trigger_rules,
                selector=selector,
                engine=RenderEngine(width, height),
                framebuffer=framebuffer,
            )
            runtime.current_template = runtime.selector.select_template(self._store.snapshot())
            self._store.set(f"{_TEMPLATE_CURRENT_PREFIX}{panel_name}", runtime.current_template)
            runtimes.append(runtime)

        return runtimes

    async def _render_loop(  # noqa: PLR0913 -- explicit orchestrator dependencies
        self,
        registry: TemplateRegistry,
        panel_runtimes: list[_PanelRuntime],
        ws_output: WebSocketOutput,
        http_output: HttpViewerOutput,
        getters_by_name: dict[str, BaseGetter],
        getter_tasks: dict[str, asyncio.Task[None]],
    ) -> None:
        """Drive render/output cycle at configured refresh rate."""
        interval = 1.0 / self._cfg.refresh_rate
        last_getter_sync = 0.0

        while not self._shutdown.is_set():
            tick_start = asyncio.get_event_loop().time()
            snapshot = self._store.snapshot()
            active_templates: set[str] = set()

            for panel in panel_runtimes:
                selected = panel.selector.select_template(snapshot)
                active_templates.add(selected)
                if selected != panel.current_template:
                    panel.current_template = selected
                    self._store.set(f"{_TEMPLATE_CURRENT_PREFIX}{panel.name}", selected)
                    _log.info("Panel '%s' switched template to '%s'", panel.name, selected)

                image = await asyncio.to_thread(
                    self._render_one,
                    registry,
                    panel.engine,
                    selected,
                )
                if image is None:
                    continue

                await asyncio.to_thread(panel.framebuffer.write, image)
                http_output.set_latest_image(panel.name, image)
                await ws_output.broadcast(image, panel=panel.name)

            if tick_start - last_getter_sync >= _GETTER_SYNC_INTERVAL_SEC:
                await self._sync_getter_tasks(
                    registry,
                    panel_runtimes,
                    active_templates,
                    getters_by_name,
                    getter_tasks,
                )
                last_getter_sync = tick_start

            elapsed = asyncio.get_event_loop().time() - tick_start
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
        """Instantiate all getter objects with shared data store."""
        if hasattr(psutil, "PROCFS_PATH"):
            psutil.PROCFS_PATH = self._cfg.procfs_path

        return [
            CpuGetter(self._store),
            GpuGetter(self._store),
            MemoryGetter(self._store),
            DiskGetter(self._store, mount=self._cfg.disk_mount),
            NetworkGetter(self._store, interfaces=self._cfg.net_interfaces),
            SystemGetter(self._store),
            FanGetter(self._store),
            SpeedtestGetter(
                self._store,
                interval=self._cfg.speedtest_interval,
                binary=self._cfg.speedtest_binary,
                server_id=self._cfg.speedtest_server_id,
                advertised_down_mbps=self._cfg.speedtest_advertised_down_mbps,
                advertised_up_mbps=self._cfg.speedtest_advertised_up_mbps,
                reference_down_mbps=self._cfg.speedtest_reference_down_mbps,
                reference_up_mbps=self._cfg.speedtest_reference_up_mbps,
                marginal_ratio=self._cfg.speedtest_marginal_ratio,
                critical_ratio=self._cfg.speedtest_critical_ratio,
            ),
            OllamaGetter(
                self._store,
                base_url=self._cfg.ollama_api_base,
                interval=self._cfg.ollama_interval,
                timeout=self._cfg.ollama_timeout,
            ),
            UpsGetter(
                self._store,
                interval=self._cfg.ups_interval,
                command=self._cfg.ups_command,
                upsc_target=self._cfg.ups_upsc_target,
            ),
        ]

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
            template_names.update(panel.rotation_templates)
            template_names.update(rule.template for rule in panel.schedule_rules)
            template_names.update(rule.template for rule in panel.trigger_rules)

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
            ("ollama.", "OllamaGetter"),
            ("ups.", "UpsGetter"),
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
