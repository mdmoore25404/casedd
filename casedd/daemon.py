"""Main async orchestrator: starts and coordinates all CASEDD subsystems.

The :class:`Daemon` class is the central coordinator.  It owns and manages:

- All data getters (CPU, GPU, memory, disk, network, system)
- The template registry (with hot-reload)
- The render engine
- All output sinks (framebuffer, WebSocket, HTTP viewer)
- All ingestion listeners (Unix socket)

The public entry point is :meth:`Daemon.run`, which blocks until a shutdown
signal (``SIGINT`` / ``SIGTERM``) is received, then performs a graceful teardown.

Public API:
    - :class:`Daemon` — top-level async coordinator
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
import signal

from PIL import Image

from casedd.config import Config
from casedd.data_store import DataStore
from casedd.getters.base import BaseGetter
from casedd.getters.cpu import CpuGetter
from casedd.getters.disk import DiskGetter
from casedd.getters.gpu import GpuGetter
from casedd.getters.memory import MemoryGetter
from casedd.getters.network import NetworkGetter
from casedd.getters.ollama import OllamaGetter
from casedd.getters.speedtest import SpeedtestGetter
from casedd.getters.system import SystemGetter
from casedd.ingestion.unix_socket import UnixSocketIngestion
from casedd.outputs.framebuffer import FramebufferOutput
from casedd.outputs.http_viewer import HttpViewerOutput
from casedd.outputs.websocket import WebSocketOutput
from casedd.renderer.engine import RenderEngine
from casedd.template.models import Template, WidgetConfig
from casedd.template.registry import TemplateRegistry

_log = logging.getLogger(__name__)

# Bind host for both WS and HTTP servers (all interfaces)
_BIND_HOST = "0.0.0.0"  # noqa: S104 — intentional; CASEDD is a local display server
_GETTER_SYNC_INTERVAL_SEC = 5.0


class Daemon:
    """Top-level CASEDD coordinator.

    Starts all subsystems in dependency order, runs the render loop at the
    configured refresh rate, and shuts everything down cleanly on signal.

    Args:
        config: Fully-loaded daemon configuration.
    """

    def __init__(self, config: Config) -> None:
        """Initialise the daemon with the provided configuration.

        Args:
            config: Daemon configuration (frozen Pydantic dataclass).
        """
        self._cfg = config
        self._store = DataStore()
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        """Start all subsystems and run the main render loop until shutdown.

        Installs signal handlers for ``SIGINT`` and ``SIGTERM``.  Performs a
        graceful teardown when the event is set.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown.set)

        # --- Build subsystems ---
        getters = self._create_getters()
        getters_by_name = {type(getter).__name__: getter for getter in getters}
        getter_tasks: dict[str, asyncio.Task[None]] = {}
        registry = TemplateRegistry(Path(self._cfg.templates_dir))
        engine = RenderEngine(self._cfg.width, self._cfg.height)

        fb_output = FramebufferOutput(
            Path(self._cfg.fb_device),
            disabled=self._cfg.no_fb,
        )
        ws_output = WebSocketOutput(_BIND_HOST, self._cfg.ws_port)
        http_output = HttpViewerOutput(
            self._store,
            _BIND_HOST,
            self._cfg.http_port,
            self._cfg.ws_port,
            self._cfg.refresh_rate,
            self._cfg.template,
            self._cfg.viewer_bg,
        )
        unix_ingestion = UnixSocketIngestion(
            Path(self._cfg.socket_path),
            self._store,
        )

        # --- Start network services ---
        await registry.start()
        await ws_output.start()
        await http_output.start()
        await unix_ingestion.start()

        # --- Start only getters required by the active template ---
        await self._sync_getter_tasks(registry, getters_by_name, getter_tasks)

        _log.info(
            "CASEDD daemon started. Template: %s | Refresh: %.1f Hz",
            self._cfg.template,
            self._cfg.refresh_rate,
        )

        try:
            await self._render_loop(
                registry,
                engine,
                fb_output,
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

    async def _render_loop(  # noqa: PLR0913 -- orchestrator loop needs explicit deps
        self,
        registry: TemplateRegistry,
        engine: RenderEngine,
        fb_output: FramebufferOutput,
        ws_output: WebSocketOutput,
        http_output: HttpViewerOutput,
        getters_by_name: dict[str, BaseGetter],
        getter_tasks: dict[str, asyncio.Task[None]],
    ) -> None:
        """Drive the render/output cycle at the configured refresh rate.

        Args:
            registry: Template registry (provides hot-reload).
            engine: Render engine (produces PIL Images).
            fb_output: Framebuffer output sink.
            ws_output: WebSocket broadcast output.
            http_output: HTTP viewer image holder.
            getters_by_name: Getter instances indexed by class name.
            getter_tasks: Active getter task map.
        """
        interval = 1.0 / self._cfg.refresh_rate
        last_getter_sync = 0.0
        while not self._shutdown.is_set():
            tick_start = asyncio.get_event_loop().time()

            if tick_start - last_getter_sync >= _GETTER_SYNC_INTERVAL_SEC:
                await self._sync_getter_tasks(registry, getters_by_name, getter_tasks)
                last_getter_sync = tick_start

            # Render in a thread pool so PIL doesn't block the event loop
            image = await asyncio.to_thread(self._render_one, registry, engine)

            if image is not None:
                # Framebuffer write is fast mmap — also in thread to be safe
                await asyncio.to_thread(fb_output.write, image)
                # HTTP snapshot is synchronous (tiny memcpy path)
                http_output.set_latest_image(image)
                # WebSocket broadcast (async, skips if no clients)
                await ws_output.broadcast(image)

            elapsed = asyncio.get_event_loop().time() - tick_start
            sleep_time = max(0.0, interval - elapsed)
            # Use wait_for so a shutdown signal interrupts the sleep
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._shutdown.wait()), timeout=sleep_time)

    def _render_one(
        self,
        registry: TemplateRegistry,
        engine: RenderEngine,
    ) -> Image.Image | None:
        """Load the active template, reload if changed, and render one frame.

        Called from a thread pool — must not use asyncio primitives.

        Args:
            registry: Template registry.
            engine: Render engine.

        Returns:
            Rendered PIL Image, or ``None`` if rendering failed.
        """
        template = registry.get(self._cfg.template)
        if template is None:
            _log.warning("Template '%s' not found — skipping frame.", self._cfg.template)
            return None

        try:
            return engine.render(template, self._store)
        except Exception:
            _log.exception("Frame render error — continuing.")
            return None

    def _create_getters(
        self,
    ) -> list[BaseGetter]:
        """Instantiate all data getter objects with the shared data store.

        Returns:
            List of initialised getter instances.
        """
        return [
            CpuGetter(self._store),
            GpuGetter(self._store),
            MemoryGetter(self._store),
            DiskGetter(self._store, mount=self._cfg.disk_mount),
            NetworkGetter(self._store, interfaces=self._cfg.net_interfaces),
            SystemGetter(self._store),
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
        ]

    async def _sync_getter_tasks(
        self,
        registry: TemplateRegistry,
        getters_by_name: dict[str, BaseGetter],
        getter_tasks: dict[str, asyncio.Task[None]],
    ) -> None:
        """Start/stop getter tasks based on sources used by active template."""
        needed = self._needed_getter_names(registry)

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

    def _needed_getter_names(self, registry: TemplateRegistry) -> set[str]:
        """Resolve which getters are required by the active template sources."""
        template = registry.get(self._cfg.template)
        if template is None:
            return set()

        names: set[str] = set()
        for source in self._template_sources(template):
            if source.startswith("cpu."):
                names.add("CpuGetter")
            elif source.startswith("nvidia."):
                names.add("GpuGetter")
            elif source.startswith("memory."):
                names.add("MemoryGetter")
            elif source.startswith("disk."):
                names.add("DiskGetter")
            elif source.startswith("net."):
                names.add("NetworkGetter")
            elif source.startswith("system."):
                names.add("SystemGetter")
            elif source.startswith("speedtest."):
                names.add("SpeedtestGetter")
            elif source.startswith("ollama."):
                names.add("OllamaGetter")
        return names

    def _template_sources(self, template: Template) -> set[str]:
        """Collect all widget source keys referenced by a template tree."""
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
