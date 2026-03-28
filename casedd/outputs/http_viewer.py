"""HTTP viewer and API surface for CASEDD.

Provides:
- Lightweight live viewer at ``GET /`` (status + panel picker only)
- Advanced app launch page at ``GET /app``
- Frame image endpoint ``GET /image`` with panel query support
- Data ingestion endpoint ``POST /api/update`` and legacy ``POST /update``
- Panel metadata endpoint ``GET /api/panels``
- Template override endpoint ``POST /api/template/override``
- Global test-mode endpoints ``GET/POST /api/test-mode``
- Simulation endpoints for replay/randomized test data
- Render buffer inspection endpoint ``GET /api/debug/render-state``
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import io
import logging
import os
from pathlib import Path
import random
import socket
import threading
import time
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import uvicorn
import yaml

from casedd.data_store import DataStore, StoreValue
from casedd.template.loader import TemplateError, load_template
from casedd.template.models import Template

_log = logging.getLogger(__name__)

_TEMPLATE_FORCE_PREFIX = "casedd.template.force."
_TEMPLATE_CURRENT_PREFIX = "casedd.template.current."
_TEST_MODE_STORE_KEY = "casedd.test_mode"


class UpdateRequest(BaseModel):
    """Payload for ``POST /api/update``."""

    model_config = ConfigDict(strict=True, frozen=True)

    update: Annotated[dict[str, object], Field(min_length=1)]


class TemplateOverrideRequest(BaseModel):
    """Payload for setting/clearing panel template override."""

    model_config = ConfigDict(strict=True, frozen=True)

    panel: str
    template: str | None = None


class TemplateSaveRequest(BaseModel):
    """Payload for saving a .casedd template."""

    model_config = ConfigDict(strict=True, frozen=True)

    template: Annotated[dict[str, object], Field(min_length=1)]


class TestModeRequest(BaseModel):
    """Payload for toggling global test mode."""

    model_config = ConfigDict(strict=True, frozen=True)

    enabled: bool


class ReplayRecord(BaseModel):
    """Replay record model for simulation playback."""

    model_config = ConfigDict(strict=True, frozen=True)

    at_ms: int = Field(ge=0)
    update: dict[str, object] = Field(min_length=1)


class ReplayStartRequest(BaseModel):
    """Payload for replay simulation mode."""

    model_config = ConfigDict(strict=True, frozen=True)

    records: list[ReplayRecord] = Field(min_length=1)
    loop: bool = False
    speed: float = Field(default=1.0, gt=0.0)


class RandomFieldSpec(BaseModel):
    """Randomized field generator specification."""

    model_config = ConfigDict(strict=True, frozen=True)

    key: str
    min: float
    max: float
    step: float = Field(default=1.0, gt=0.0)


class RandomStartRequest(BaseModel):
    """Payload for random simulation mode."""

    model_config = ConfigDict(strict=True, frozen=True)

    interval: float = Field(default=1.0, gt=0.0)
    fields: list[RandomFieldSpec] = Field(min_length=1)


class _FrameStore:
    """Thread-safe per-panel PNG frame holder."""

    def __init__(self) -> None:
        """Initialize empty frame map."""
        self._lock = threading.Lock()
        self._frames: dict[str, bytes] = {}

    def set(self, panel: str, data: bytes) -> None:
        """Store a panel PNG frame."""
        with self._lock:
            self._frames[panel] = data

    def get(self, panel: str) -> bytes | None:
        """Get latest frame bytes for one panel."""
        with self._lock:
            return self._frames.get(panel)


class _SimulationController:
    """Runs replay/random simulation streams that write into DataStore."""

    def __init__(self, store: DataStore) -> None:
        """Initialize simulation controller.

        Args:
            store: Shared CASEDD data store.
        """
        self._store = store
        self._task: asyncio.Task[None] | None = None
        self._mode: str = "idle"
        self._started_at: float = 0.0

    def status(self) -> dict[str, object]:
        """Return current simulation status payload."""
        running = self._task is not None and not self._task.done()
        return {
            "running": running,
            "mode": self._mode,
            "started_at": self._started_at,
        }

    async def stop(self) -> None:
        """Stop any active simulation task."""
        if self._task is None:
            self._mode = "idle"
            return
        if not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._mode = "idle"

    async def start_replay(self, request: ReplayStartRequest) -> None:
        """Start replay simulation.

        Args:
            request: Replay request model.
        """
        await self.stop()
        self._mode = "replay"
        self._started_at = time.time()
        self._task = asyncio.create_task(self._run_replay(request), name="sim-replay")

    async def start_random(self, request: RandomStartRequest) -> None:
        """Start randomized simulation.

        Args:
            request: Random simulation request model.
        """
        await self.stop()
        self._mode = "random"
        self._started_at = time.time()
        self._task = asyncio.create_task(self._run_random(request), name="sim-random")

    async def _run_replay(self, request: ReplayStartRequest) -> None:
        """Run replay loop until completion/cancel."""
        sorted_records = sorted(request.records, key=lambda item: item.at_ms)
        while True:
            start = asyncio.get_event_loop().time()
            last_ms = 0
            for record in sorted_records:
                delta_ms = max(0, record.at_ms - last_ms)
                sleep_time = (delta_ms / 1000.0) / request.speed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                payload = _normalize_update_payload(record.update)
                if payload:
                    self._store.update(payload)
                last_ms = record.at_ms
            if not request.loop:
                break
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed < 0.01:
                await asyncio.sleep(0.01)

    async def _run_random(self, request: RandomStartRequest) -> None:
        """Run random walk simulation until canceled."""
        values: dict[str, float] = {}
        for field in request.fields:
            values[field.key] = (field.min + field.max) / 2.0

        while True:
            payload: dict[str, StoreValue] = {}
            for field in request.fields:
                current = values[field.key]
                direction = random.choice((-1.0, 1.0))  # noqa: S311 -- simulation only
                next_value = current + (field.step * direction)
                next_value = min(field.max, max(field.min, next_value))
                values[field.key] = next_value
                payload[field.key] = round(next_value, 4)
            self._store.update(payload)
            await asyncio.sleep(request.interval)


_LIGHT_VIEWER_HTML = """\
<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>CASEDD Live Viewer</title>
  <style>
    body {
      margin: 0;
      background: __VIEWER_BG__;
      color: #d0d7de;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
      padding: 10px;
    }
    #toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      width: min(100%, 960px);
      flex-wrap: wrap;
      font-size: 13px;
    }
    #frame {
      width: min(100%, 960px);
      height: auto;
      border: 1px solid #30363d;
      border-radius: 8px;
      background: #000;
      image-rendering: pixelated;
    }
    select, a {
      background: #161b22;
      color: #d0d7de;
      border: 1px solid #30363d;
      border-radius: 6px;
      padding: 4px 8px;
      text-decoration: none;
    }
  </style>
</head>
<body>
  <div id=\"toolbar\">
    <label for=\"panel\">Panel</label>
    <select id=\"panel\"></select>
    <span id=\"meta\"></span>
    <a href=\"/app\" target=\"_blank\" rel=\"noreferrer\">Advanced App</a>
  </div>
  <img id=\"frame\" src=\"/image\" alt=\"CASEDD frame\" />
  <script>
    const frame = document.getElementById('frame');
    const panelSelect = document.getElementById('panel');
    const meta = document.getElementById('meta');

    let panelName = '';
    let ws = null;

    function wsUrl() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      return `${proto}://${location.hostname}:__WS_PORT__/ws`;
    }

    async function loadPanels() {
      const response = await fetch('/api/panels', { cache: 'no-store' });
      const payload = await response.json();
      const panels = Array.isArray(payload.panels) ? payload.panels : [];
      panelSelect.innerHTML = '';
      for (const panel of panels) {
        const option = document.createElement('option');
        option.value = panel.name;
        option.textContent = panel.display_name || panel.name;
        panelSelect.appendChild(option);
      }
      panelName = payload.default_panel || (panels[0] ? panels[0].name : '');
      panelSelect.value = panelName;
            meta.textContent =
                'panel: ' + panelName + ' | state: ' + (payload.test_mode ? 'test-mode' : 'live');
      refreshFrame();
    }

    function refreshFrame() {
      if (!panelName) {
        return;
      }
      frame.src = '/image?panel=' + encodeURIComponent(panelName) + '&t=' + Date.now();
    }

    function connectWs() {
      if (ws) {
        ws.close();
      }
      ws = new WebSocket(wsUrl());
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type !== 'frame' || !msg.data) {
            return;
          }
          if (msg.panel && msg.panel !== panelName) {
            return;
          }
          frame.src = 'data:image/png;base64,' + msg.data;
        } catch (_err) {
          refreshFrame();
        }
      };
      ws.onclose = () => {
        setTimeout(connectWs, 1000);
      };
      ws.onerror = () => {
        ws.close();
      };
    }

    panelSelect.addEventListener('change', () => {
      panelName = panelSelect.value;
      refreshFrame();
    });

    loadPanels().then(connectWs).catch(() => {
      setInterval(refreshFrame, 1000);
    });
  </script>
</body>
</html>
"""


_ADVANCED_APP_PORT = int(os.environ.get("CASEDD_APP_PORT", "5173"))


def _advanced_app_unavailable_html(port: int) -> str:
        """Build fallback HTML when Vite dev server is unavailable.

        Args:
                port: Expected Vite development server port.

        Returns:
                Minimal user-facing HTML page.
        """
        return f"""\
<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>CASEDD Advanced App</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background: #0d1117;
            color: #d0d7de;
            font-family: ui-sans-serif, system-ui, sans-serif;
        }}
        code {{ color: #8b949e; }}
    </style>
</head>
<body>
    <h2>Advanced App Is Not Running</h2>
    <p>
        CASEDD expected the Vite dev server on port <code>{port}</code>, but it was not reachable.
    </p>
    <p>
        Start it with <code>./dev.sh restart</code> or manually run
        <code>cd web && npm install && npm run dev -- --host 0.0.0.0 --port {port}</code>.
    </p>
    <p><a href=\"/\">Back to lightweight viewer</a></p>
</body>
</html>
"""


def _is_local_port_open(host: str, port: int) -> bool:
        """Check whether a TCP host/port is reachable.

        Args:
                host: Host name or IP.
                port: TCP port.

        Returns:
                ``True`` when a connection succeeds.
        """
        try:
                with socket.create_connection((host, port), timeout=0.25):
                        return True
        except OSError:
                return False


def _build_app(  # noqa: PLR0913,PLR0915 -- explicit app wiring keeps routes discoverable
    store: DataStore,
    frame_store: _FrameStore,
    ws_port: int,
    panels: list[dict[str, object]],
    default_panel: str,
    viewer_bg: str,
    templates_dir: Path,
    history_provider: Callable[[], dict[str, object]],
    simulation: _SimulationController,
) -> FastAPI:
    """Build and configure FastAPI app for viewer and control APIs."""
    app = FastAPI(
        title="CASEDD HTTP Viewer",
        version="0.2.0",
        description="CASEDD live viewer, panel controls, and test simulation APIs.",
    )

    panel_names = [str(panel["name"]) for panel in panels]

    def _template_path(name: str) -> Path:
        return templates_dir / f"{name}.casedd"

    def _load_template_payload(name: str) -> dict[str, object]:
        path = _template_path(name)
        try:
            template = load_template(path)
        except TemplateError as exc:
            if "does not exist" in exc.reason:
                raise HTTPException(
                    status_code=404,
                    detail=f"Unknown template '{name}'",
                ) from exc
            raise HTTPException(
                status_code=422,
                detail=f"Template '{name}' is invalid: {exc.reason}",
            ) from exc
        return template.model_dump(mode="json")

    @app.get("/", response_class=HTMLResponse, summary="Lightweight viewer")
    async def root() -> str:
        return _LIGHT_VIEWER_HTML.replace("__VIEWER_BG__", viewer_bg).replace(
            "__WS_PORT__",
            str(ws_port),
        )

    @app.get("/app", response_class=HTMLResponse, summary="Advanced app entrypoint")
    async def app_page(request: Request) -> Response:
        host = request.url.hostname or "localhost"
        if not _is_local_port_open(host, _ADVANCED_APP_PORT):
            return HTMLResponse(
                content=_advanced_app_unavailable_html(_ADVANCED_APP_PORT),
                status_code=503,
            )
        app_url = f"{request.url.scheme}://{host}:{_ADVANCED_APP_PORT}/"
        return RedirectResponse(url=app_url, status_code=307)

    @app.get("/image", summary="Latest rendered PNG")
    async def image(
        panel: str = Query(default=default_panel, description="Panel name to view"),
    ) -> Response:
        if panel not in panel_names:
            return Response(status_code=404, content=b"Unknown panel")
        data = frame_store.get(panel)
        if data is None:
            return Response(status_code=503, content=b"Frame not ready yet")
        return Response(content=data, media_type="image/png")

    @app.get("/api/panels", summary="List configured panels")
    async def get_panels() -> dict[str, object]:
        return {
            "default_panel": default_panel,
            "test_mode": _is_test_mode(store.get(_TEST_MODE_STORE_KEY)),
            "panels": [
                {
                    **panel,
                    "current_template": store.get(
                        f"{_TEMPLATE_CURRENT_PREFIX}{panel['name']}",
                        "",
                    ),
                    "forced_template": store.get(
                        f"{_TEMPLATE_FORCE_PREFIX}{panel['name']}",
                        "",
                    ),
                }
                for panel in panels
            ],
        }

    @app.post("/api/update", status_code=204, summary="Push data update")
    async def push_update(body: UpdateRequest) -> None:
        payload = _normalize_update_payload(body.update)
        if not payload:
            msg = "update payload has no valid primitive values"
            raise HTTPException(status_code=422, detail=msg)
        store.update(payload)

    @app.post("/update", status_code=204, include_in_schema=False)
    async def legacy_update(body: UpdateRequest) -> None:
        payload = _normalize_update_payload(body.update)
        if not payload:
            msg = "update payload has no valid primitive values"
            raise HTTPException(status_code=422, detail=msg)
        store.update(payload)

    @app.post("/api/template/override", summary="Set/clear per-panel template override")
    async def set_template_override(body: TemplateOverrideRequest) -> dict[str, object]:
        if body.panel not in panel_names:
            raise HTTPException(status_code=404, detail=f"Unknown panel '{body.panel}'")

        available_templates = {path.stem for path in templates_dir.glob("*.casedd")}
        key = f"{_TEMPLATE_FORCE_PREFIX}{body.panel}"
        if body.template is None or body.template.strip().lower() == "auto":
            store.set(key, "")
            return {"status": "ok", "mode": "auto"}

        selected = body.template.strip()
        if selected not in available_templates:
            raise HTTPException(status_code=400, detail=f"Unknown template '{selected}'")
        store.set(key, selected)
        return {"status": "ok", "mode": "forced", "template": selected}

    @app.get("/api/templates", summary="List available templates")
    async def list_templates() -> dict[str, object]:
        templates = sorted(path.stem for path in templates_dir.glob("*.casedd"))
        return {"templates": templates}

    @app.get("/api/templates/{name}", summary="Get template model for editing")
    async def get_template(name: str) -> dict[str, object]:
        payload = _load_template_payload(name)
        return {
            "name": name,
            "path": str(_template_path(name)),
            "template": payload,
        }

    @app.put("/api/templates/{name}", summary="Validate and save template")
    async def put_template(name: str, body: TemplateSaveRequest) -> dict[str, object]:
        candidate = dict(body.template)
        candidate["name"] = name

        try:
            validated = Template.model_validate(candidate)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Template validation failed: {exc}",
            ) from exc

        output = validated.model_dump(mode="json")
        yaml_text = yaml.safe_dump(output, sort_keys=False)

        path = _template_path(name)
        try:
            path.write_text(yaml_text, encoding="utf-8")
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Could not write template '{name}': {exc}",
            ) from exc

        return {
            "status": "ok",
            "name": name,
            "path": str(path),
            "template": output,
        }

    @app.get("/api/test-mode", summary="Get global test mode")
    async def get_test_mode() -> dict[str, object]:
        return {"enabled": _is_test_mode(store.get(_TEST_MODE_STORE_KEY))}

    @app.post("/api/test-mode", summary="Set global test mode")
    async def set_test_mode(body: TestModeRequest) -> dict[str, object]:
        store.set(_TEST_MODE_STORE_KEY, 1 if body.enabled else 0)
        return {"enabled": body.enabled}

    @app.post("/api/sim/replay", summary="Start replay simulation")
    async def start_replay(body: ReplayStartRequest) -> dict[str, object]:
        await simulation.start_replay(body)
        return simulation.status()

    @app.post("/api/sim/random", summary="Start random simulation")
    async def start_random(body: RandomStartRequest) -> dict[str, object]:
        await simulation.start_random(body)
        return simulation.status()

    @app.post("/api/sim/stop", summary="Stop active simulation")
    async def stop_sim() -> dict[str, object]:
        await simulation.stop()
        return simulation.status()

    @app.get("/api/sim/status", summary="Get simulation status")
    async def sim_status() -> dict[str, object]:
        return simulation.status()

    @app.get("/api/debug/render-state", summary="Inspect renderer history buffers")
    async def debug_render_state() -> dict[str, object]:
        return history_provider()

    return app


class HttpViewerOutput:
    """Manage HTTP server lifecycle and per-panel frame storage."""

    def __init__(  # noqa: PLR0913 -- runtime wiring dependencies are explicit
        self,
        store: DataStore,
        host: str,
        port: int,
        ws_port: int,
        panels: list[dict[str, object]],
        default_panel: str,
        viewer_bg: str,
        templates_dir: Path,
        history_provider: Callable[[], dict[str, object]],
    ) -> None:
        """Initialize HTTP viewer output."""
        self._host = host
        self._port = port
        self._frame_store = _FrameStore()
        self._simulation = _SimulationController(store)
        self._app = _build_app(
            store=store,
            frame_store=self._frame_store,
            ws_port=ws_port,
            panels=panels,
            default_panel=default_panel,
            viewer_bg=viewer_bg,
            templates_dir=templates_dir,
            history_provider=history_provider,
            simulation=self._simulation,
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start uvicorn server task."""
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._task = asyncio.create_task(server.serve(), name="casedd-http-server")
        _log.info("HTTP viewer started on http://%s:%d/", self._host, self._port)

    async def stop(self) -> None:
        """Stop HTTP server and active simulation task."""
        await self._simulation.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        _log.info("HTTP server stopped.")

    def set_latest_image(self, panel: str, image: Image.Image) -> None:
        """Store latest rendered image bytes for one panel."""
        assert isinstance(image, Image.Image)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=False)
        self._frame_store.set(panel, buf.getvalue())


def _is_test_mode(raw: StoreValue | None) -> bool:
    """Convert store value to test-mode boolean."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() not in {"", "0", "false", "no", "off"}
    return False


def _to_store_value(value: object) -> StoreValue | None:
    """Convert arbitrary JSON-like value to supported store primitive."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (float, int, str)):
        return value
    return None


def _flatten_update(
    mapping: dict[str, object],
    out: dict[str, StoreValue],
    prefix: str = "",
) -> None:
    """Flatten nested update objects into dotted keys."""
    for key_obj, value in mapping.items():
        if not isinstance(key_obj, str):
            continue
        key = key_obj.strip()
        if not key:
            continue

        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            nested: dict[str, object] = {}
            for nested_key, nested_value in value.items():
                if isinstance(nested_key, str):
                    nested[nested_key] = nested_value
            if nested:
                _flatten_update(nested, out, full_key)
            continue

        coerced = _to_store_value(value)
        if coerced is not None:
            out[full_key] = coerced


def _normalize_update_payload(update: dict[str, object]) -> dict[str, StoreValue]:
    """Normalize update payload to flat dotted-key primitives."""
    flat: dict[str, StoreValue] = {}
    _flatten_update(update, flat)
    return flat
