"""HTTP viewer: serves the live display image and accepts data updates.

Provides a lightweight FastAPI HTTP application with three routes:

- ``GET /`` — HTML page with auto-refreshing live view via WebSocket.
- ``GET /image`` — Current display frame as a raw PNG (for polling clients).
- ``POST /update`` — Accepts JSON data payloads and writes them to the store.
- ``GET /docs`` — Auto-generated OpenAPI documentation (FastAPI default).

The HTTP server is run by uvicorn alongside the WebSocket server.  The
:class:`HttpViewerOutput` exposes a ``set_latest_image`` method that the
render loop calls after every frame, making the latest PNG available over HTTP.

Public API:
    - :class:`UpdateRequest` — Pydantic request model for ``POST /update``
    - :class:`HttpViewerOutput` — manages the HTTP server lifecycle
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import threading
from typing import Annotated

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from casedd.data_store import DataStore, StoreValue

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

_FIELD_DESCRIPTION = "Flat mapping of dotted data-store keys to new values."


class UpdateRequest(BaseModel):
    """Payload for the ``POST /update`` endpoint.

    Attributes:
        update: Flat mapping of dotted data-store keys → new values.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    update: Annotated[
        dict[str, StoreValue],
        Field(description=_FIELD_DESCRIPTION, min_length=1),
    ]


# ---------------------------------------------------------------------------
# HTML template for the live viewer page
# ---------------------------------------------------------------------------

_VIEWER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>CASEDD Live Viewer</title>
  <style>
        :root {{
            --overlay-bg: rgba(9, 11, 14, 0.88);
            --overlay-border: #2d333b;
            --overlay-fg: #c8d0d9;
            --overlay-muted: #8b949e;
            --overlay-good: #3fb950;
            --overlay-warn: #d29922;
            --overlay-bad: #f85149;
            --btn-bg: #1b222c;
            --btn-fg: #c8d0d9;
            --btn-border: #30363d;
        }}
        body {{
            margin: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            color: var(--overlay-fg);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            transition: background-color 140ms linear;
        }}
        #frame {{
            max-width: 100%;
            max-height: 100%;
            image-rendering: pixelated;
            display: block;
        }}
        #status {{
            position: fixed;
            top: 10px;
            right: 12px;
            border: 1px solid var(--overlay-border);
            border-radius: 8px;
            background: var(--overlay-bg);
            color: var(--overlay-fg);
            font-size: 12px;
            line-height: 1.35;
            backdrop-filter: blur(2px);
            cursor: pointer;
            user-select: none;
            min-width: 132px;
            box-shadow: 0 8px 28px rgba(0, 0, 0, 0.32);
        }}
        #status.compact {{
            padding: 7px 10px;
        }}
        #status.expanded {{
            min-width: 280px;
            padding: 8px 10px;
            cursor: default;
        }}
        #status .row {{
            display: flex;
            justify-content: space-between;
            gap: 8px;
            white-space: nowrap;
        }}
        #status .k {{ color: var(--overlay-muted); }}
        #status .ok {{ color: var(--overlay-good); }}
        #status .warn {{ color: var(--overlay-warn); }}
        #status .bad {{ color: var(--overlay-bad); }}
        #status .details {{
            display: grid;
            gap: 3px;
            margin-top: 6px;
        }}
        #status .controls {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 8px;
        }}
        #status .pushbox {{
            display: grid;
            gap: 6px;
            margin-top: 8px;
            padding-top: 8px;
            border-top: 1px solid var(--overlay-border);
        }}
        #status .pushrow {{
            display: flex;
            gap: 6px;
        }}
        #status input,
        #status select {{
            border: 1px solid var(--btn-border);
            background: #0f141b;
            color: var(--overlay-fg);
            border-radius: 4px;
            padding: 3px 6px;
            font-size: 11px;
            font-family: inherit;
            min-width: 0;
        }}
        #status textarea {{
            width: 100%;
            min-height: 80px;
            resize: vertical;
            border: 1px solid var(--btn-border);
            background: #0f141b;
            color: var(--overlay-fg);
            border-radius: 4px;
            padding: 5px 6px;
            font-size: 11px;
            font-family: inherit;
            box-sizing: border-box;
        }}
        #status #pushKey {{
            flex: 1 1 58%;
        }}
        #status #pushValue {{
            flex: 1 1 42%;
        }}
        #status #pushResult {{
            color: var(--overlay-muted);
            min-height: 14px;
        }}
        #status button {{
            border: 1px solid var(--btn-border);
            background: var(--btn-bg);
            color: var(--btn-fg);
            border-radius: 4px;
            padding: 2px 7px;
            font-size: 11px;
            font-family: inherit;
            cursor: pointer;
        }}
        #status button:hover {{
            filter: brightness(1.12);
        }}
  </style>
</head>
<body>
  <img id="frame" src="/image" alt="CASEDD frame" />
    <div id="status" class="compact" title="Click for details"></div>
  <script>
        const cfg = Object.freeze({
            wsPort: __CASEDD_WS_PORT__,
            refreshHz: __CASEDD_REFRESH_RATE__,
            template: __CASEDD_TEMPLATE_JSON__,
            viewerBg: __CASEDD_VIEWER_BG_JSON__,
            advancedPayload: __CASEDD_ADV_PAYLOAD_JSON__
        });

        const img = document.getElementById('frame');
        const status = document.getElementById('status');

        let ws = null;
        let reconnectTimer = null;
        let reconnectDelayMs = 1000;
        let connected = false;
        let lastFrameAt = 0;
        let frameCount = 0;
        let fps = 0;
        let isExpanded = false;
        const LIVE_FRAME_AGE_MS = 2500;

        function wsUrl() {
            const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
            return scheme + '://' + location.hostname + ':' + cfg.wsPort + '/ws';
        }

        function formatClock(tsMs) {
            if (tsMs <= 0) {
                return 'n/a';
            }
            const d = new Date(tsMs);
            return d.toLocaleTimeString();
        }

        function statusState() {
            if (connected) {
                return ['live', 'ok'];
            }
            if (lastFrameAt > 0 && Date.now() - lastFrameAt < LIVE_FRAME_AGE_MS) {
                return ['live', 'ok'];
            }
            if (reconnectTimer !== null) {
                return ['reconnecting', 'warn'];
            }
            return ['offline', 'bad'];
        }

        function applyViewerBackground(color) {
            document.body.style.backgroundColor = color;
            localStorage.setItem('caseddViewerBg', color);
        }

        function currentViewerBackground() {
            return localStorage.getItem('caseddViewerBg') || cfg.viewerBg;
        }

        function setTheme(kind) {
            if (kind === 'light') {
                applyViewerBackground('#ffffff');
            } else if (kind === 'dark') {
                applyViewerBackground('#000000');
            } else {
                applyViewerBackground(cfg.viewerBg);
            }
            renderStatus();
        }

        function renderStatus() {
            if (isExpanded && statusEditorHasFocus()) {
                return;
            }
            const [stateLabel, stateClass] = statusState();
            const compact = '<span class="' + stateClass + '">' + stateLabel + '</span>';
                        const row = (k, v) =>
                            '<div class="row"><span class="k">' +
                            k +
                            '</span><span>' +
                            v +
                            '</span></div>';
                        const rowState = (k, klass, v) =>
                            '<div class="row"><span class="k">' +
                            k +
                            '</span><span class="' +
                            klass +
                            '">' +
                            v +
                            '</span></div>';

            if (!isExpanded) {
                status.className = 'compact';
                status.title = 'Click for details';
                status.innerHTML = compact;
                return;
            }

            const ageMs = lastFrameAt > 0 ? Date.now() - lastFrameAt : null;
            const ageText = ageMs === null ? 'n/a' : (ageMs / 1000).toFixed(1) + 's ago';
            const tsText = formatClock(lastFrameAt);

            status.className = 'expanded';
            status.title = 'Click header to collapse';
            status.innerHTML = [
                rowState('state', stateClass, stateLabel),
                '<div class="details">',
                    row('template', cfg.template),
                    row('render', cfg.refreshHz.toFixed(1) + ' Hz'),
                    row('viewer fps', fps.toFixed(1)),
                    row('last update', tsText),
                    row('last frame age', ageText),
                '</div>',
                '<div class="controls">',
                    '<button id="bgDefaultBtn" type="button">BG default</button>',
                    '<button id="bgDarkBtn" type="button">BG black</button>',
                    '<button id="bgLightBtn" type="button">BG white</button>',
                    '<button id="collapseBtn" type="button">Hide details</button>',
                '</div>',
                '<div class="pushbox">',
                    '<div class="k">push test update</div>',
                    '<div class="pushrow">',
                        '<input id="pushKey" type="text" value="outside_temp_f" />',
                        '<input id="pushValue" type="text" value="10" />',
                    '</div>',
                    '<div class="pushrow">',
                        '<select id="pushType">',
                            '<option value="number">number</option>',
                            '<option value="string">string</option>',
                            '<option value="boolean">boolean</option>',
                            '<option value="json">json</option>',
                        '</select>',
                        '<button id="pushSendBtn" type="button">Push</button>',
                    '</div>',
                    '<div id="pushResult"></div>',
                    '<div class="k">advanced json payload</div>',
                    '<textarea id="pushJsonPayload">' +
                        cfg.advancedPayload +
                    '</textarea>',
                    '<div class="pushrow">',
                        '<button id="pushJsonValidateBtn" type="button">Validate JSON</button>',
                        '<button id="pushJsonSendBtn" type="button" disabled>Push JSON</button>',
                    '</div>',
                    '<div id="pushJsonResult"></div>',
                '</div>'
            ].join('');

            document.getElementById('bgDefaultBtn').onclick = (ev) => {
                ev.stopPropagation();
                setTheme('default');
            };
            document.getElementById('bgDarkBtn').onclick = (ev) => {
                ev.stopPropagation();
                setTheme('dark');
            };
            document.getElementById('bgLightBtn').onclick = (ev) => {
                ev.stopPropagation();
                setTheme('light');
            };
            document.getElementById('collapseBtn').onclick = (ev) => {
                ev.stopPropagation();
                isExpanded = false;
                renderStatus();
            };
            document.getElementById('pushSendBtn').onclick = async (ev) => {
                ev.stopPropagation();
                await sendPushUpdate();
            };
            const jsonPayloadNode = document.getElementById('pushJsonPayload');
            const jsonValidateBtn = document.getElementById('pushJsonValidateBtn');
            const jsonSendBtn = document.getElementById('pushJsonSendBtn');
            if (jsonPayloadNode && jsonValidateBtn && jsonSendBtn) {
                jsonPayloadNode.onclick = (ev) => ev.stopPropagation();
                jsonPayloadNode.oninput = () => validateAdvancedPayload(false);
                jsonValidateBtn.onclick = async (ev) => {
                    ev.stopPropagation();
                    validateAdvancedPayload(true);
                };
                jsonSendBtn.onclick = async (ev) => {
                    ev.stopPropagation();
                    await sendAdvancedPushUpdate();
                };
            }
            validateAdvancedPayload(false);
        }

        function parseAdvancedPayload() {
            const payloadNode = document.getElementById('pushJsonPayload');
            if (!payloadNode) {
                throw new Error('JSON editor not found');
            }
            const parsed = JSON.parse(payloadNode.value);
            if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
                throw new Error('payload must be a JSON object');
            }
            return parsed;
        }

        function validateAdvancedPayload(showOk) {
            const resultNode = document.getElementById('pushJsonResult');
            const sendBtn = document.getElementById('pushJsonSendBtn');
            if (!resultNode || !sendBtn) {
                return false;
            }
            try {
                parseAdvancedPayload();
                sendBtn.disabled = false;
                if (showOk) {
                    resultNode.textContent = 'JSON is valid';
                    resultNode.className = 'ok';
                } else {
                    resultNode.textContent = '';
                    resultNode.className = '';
                }
                return true;
            } catch (err) {
                sendBtn.disabled = true;
                resultNode.textContent = 'invalid JSON: ' + err.message;
                resultNode.className = 'bad';
                return false;
            }
        }

        async function sendAdvancedPushUpdate() {
            const resultNode = document.getElementById('pushJsonResult');
            if (!resultNode) {
                return;
            }
            let payload;
            try {
                payload = parseAdvancedPayload();
            } catch (err) {
                resultNode.textContent = 'invalid JSON: ' + err.message;
                resultNode.className = 'bad';
                return;
            }
            try {
                const response = await fetch('/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (!response.ok) {
                    resultNode.textContent = 'push failed: HTTP ' + response.status;
                    resultNode.className = 'bad';
                    return;
                }
                resultNode.textContent =
                    'advanced payload pushed @ ' + new Date().toLocaleTimeString();
                resultNode.className = 'ok';
            } catch (_err) {
                resultNode.textContent = 'push failed: network error';
                resultNode.className = 'bad';
            }
        }

        function coercePushValue(raw, kind) {
            if (kind === 'number') {
                const n = Number(raw);
                if (Number.isNaN(n)) {
                    throw new Error('value is not a number');
                }
                return n;
            }
            if (kind === 'boolean') {
                const v = String(raw).toLowerCase();
                if (v === 'true' || v === '1' || v === 'yes') {
                    return true;
                }
                if (v === 'false' || v === '0' || v === 'no') {
                    return false;
                }
                throw new Error('value is not a boolean');
            }
            if (kind === 'json') {
                return JSON.parse(raw);
            }
            return raw;
        }

        async function sendPushUpdate() {
            const keyInput = document.getElementById('pushKey');
            const valueInput = document.getElementById('pushValue');
            const typeInput = document.getElementById('pushType');
            const resultNode = document.getElementById('pushResult');
            if (!keyInput || !valueInput || !typeInput || !resultNode) {
                return;
            }

            const key = keyInput.value.trim();
            const rawValue = valueInput.value;
            if (!key) {
                resultNode.textContent = 'key is required';
                resultNode.className = 'bad';
                return;
            }

            let value;
            try {
                value = coercePushValue(rawValue, typeInput.value);
            } catch (err) {
                resultNode.textContent = 'parse error: ' + err.message;
                resultNode.className = 'bad';
                return;
            }

            try {
                const response = await fetch('/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ update: { [key]: value } })
                });
                if (!response.ok) {
                    resultNode.textContent = 'push failed: HTTP ' + response.status;
                    resultNode.className = 'bad';
                    return;
                }
                resultNode.textContent = 'pushed ' + key + ' @ ' + new Date().toLocaleTimeString();
                resultNode.className = 'ok';
            } catch (_err) {
                resultNode.textContent = 'push failed: network error';
                resultNode.className = 'bad';
            }
        }

        function scheduleReconnect() {
            if (reconnectTimer !== null) {
                return;
            }
            renderStatus();
            reconnectTimer = setTimeout(() => {
                reconnectTimer = null;
                connect();
            }, reconnectDelayMs);
            reconnectDelayMs = Math.min(Math.floor(reconnectDelayMs * 1.6), 10000);
        }

        function resetBackoff() {
            reconnectDelayMs = 1000;
            if (reconnectTimer !== null) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
        }

        function noteFrame() {
            lastFrameAt = Date.now();
            frameCount += 1;
        }

        function statusEditorHasFocus() {
            const active = document.activeElement;
            if (!active || !status.contains(active)) {
                return false;
            }
            const tag = active.tagName;
            return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
        }

        function connect() {
            try {
                ws = new WebSocket(wsUrl());
            } catch (_err) {
                connected = false;
                scheduleReconnect();
                return;
            }

            renderStatus();

            ws.onopen = () => {
                connected = true;
                resetBackoff();
                renderStatus();
            };

            ws.onclose = () => {
                connected = false;
                scheduleReconnect();
            };

            ws.onerror = () => {
                connected = false;
                if (ws) {
                    ws.close();
                }
            };

            ws.onmessage = (ev) => {
                try {
                    const msg = JSON.parse(ev.data);
                    if (msg.type === 'frame' && typeof msg.data === 'string') {
                        img.src = 'data:image/png;base64,' + msg.data;
                        noteFrame();
                    }
                } catch (_err) {
                    // Ignore malformed payloads; keep connection alive.
                }
            };
        }

        setInterval(async () => {
            const staleMs = Date.now() - lastFrameAt;
            if (!connected || staleMs > LIVE_FRAME_AGE_MS) {
                try {
                    const resp = await fetch('/image?t=' + Date.now(), { cache: 'no-store' });
                    if (resp.ok) {
                        const blob = await resp.blob();
                        const url = URL.createObjectURL(blob);
                        img.src = url;
                        setTimeout(() => URL.revokeObjectURL(url), 1000);
                        noteFrame();
                    }
                } catch (_err) {
                    // Ignore transient HTTP errors while daemon is restarting.
                }
            }
        }, 1000);

        setInterval(() => {
            fps = frameCount;
            frameCount = 0;
            renderStatus();
        }, 500);

        status.addEventListener('click', (ev) => {
            if (!isExpanded) {
                isExpanded = true;
                renderStatus();
                return;
            }
            if (ev.target === status) {
                isExpanded = false;
                renderStatus();
            }
        });

        document.addEventListener('click', (ev) => {
            if (!isExpanded) {
                return;
            }
            if (!status.contains(ev.target)) {
                isExpanded = false;
                renderStatus();
            }
        });

        document.addEventListener('keydown', (ev) => {
            if (ev.key === 'Escape' && isExpanded) {
                isExpanded = false;
                renderStatus();
            }
        });

        applyViewerBackground(currentViewerBackground());
        renderStatus();
        connect();
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def _build_app(  # noqa: PLR0913 — explicit params keep wiring clear at callsite
    store: DataStore,
    frame_holder: _FrameHolder,
    ws_port: int,
    refresh_rate: float,
    template_name: str,
    viewer_bg: str,
) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        store: Shared data store for write operations from ``/update``.
        frame_holder: Shared holder for the latest rendered PNG bytes.
        ws_port: WebSocket port to embed in the viewer HTML.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title="CASEDD HTTP Viewer",
        version="0.1.0",
        description=(
            "CASEDD — Case Display Daemon live viewer and data ingestion API."
        ),
    )

    @app.get("/", response_class=HTMLResponse, summary="Live display viewer")
    async def root() -> str:
        """Return the browser live-view page with embedded WebSocket client."""
        starter_payload = json.dumps(
            {
                "update": {
                    "outside_temp_f": 72.0,
                    "custom.note": "hello",
                }
            }
        )
        html = _VIEWER_HTML.replace("{{", "{").replace("}}", "}")
        html = html.replace("__CASEDD_WS_PORT__", str(ws_port))
        html = html.replace("__CASEDD_REFRESH_RATE__", f"{refresh_rate:.3f}")
        html = html.replace("__CASEDD_TEMPLATE_JSON__", json.dumps(template_name))
        html = html.replace("__CASEDD_VIEWER_BG_JSON__", json.dumps(viewer_bg))
        return html.replace("__CASEDD_ADV_PAYLOAD_JSON__", json.dumps(starter_payload))

    @app.get("/image", summary="Current display frame (PNG)")
    async def current_image() -> Response:
        """Return the most recently rendered frame as a PNG image.

        Returns:
            PNG image response, or a 503 if no frame has been rendered yet.
        """
        data = frame_holder.get()
        if data is None:
            return Response(status_code=503, content=b"No frame available yet.")
        return Response(content=data, media_type="image/png")

    @app.post("/update", status_code=204, summary="Push data-store values")
    async def update(body: UpdateRequest) -> None:
        """Write key/value pairs to the live data store.

        Args:
            body: JSON object with an ``update`` mapping of dotted keys.
        """
        store.update(body.update)
        _log.debug("REST /update: wrote %d key(s).", len(body.update))

    return app


# ---------------------------------------------------------------------------
# Frame holder (thread-safe latest PNG storage)
# ---------------------------------------------------------------------------


class _FrameHolder:
    """Thread-safe container for the latest rendered PNG bytes."""

    def __init__(self) -> None:
        """Initialise with no frame."""
        self._lock = threading.Lock()
        self._data: bytes | None = None

    def set(self, data: bytes) -> None:
        """Replace the stored frame.

        Args:
            data: PNG-encoded image bytes.
        """
        with self._lock:
            self._data = data

    def get(self) -> bytes | None:
        """Return the latest frame bytes, or ``None`` if not yet available.

        Returns:
            PNG bytes or ``None``.
        """
        with self._lock:
            return self._data


# ---------------------------------------------------------------------------
# Public output class
# ---------------------------------------------------------------------------


class HttpViewerOutput:
    """Manages the HTTP viewer server lifecycle.

    Args:
        store: Data store instance shared with the rest of the daemon.
        host: Bind host for the uvicorn HTTP server.
        port: HTTP port to listen on.
        ws_port: WebSocket port, embedded in the viewer HTML.
    """

    def __init__(  # noqa: PLR0913 — constructor mirrors runtime wiring dependencies
        self,
        store: DataStore,
        host: str,
        port: int,
        ws_port: int,
        refresh_rate: float,
        template_name: str,
        viewer_bg: str,
    ) -> None:
        """Initialise the HTTP viewer output.

        Args:
            store: Shared data store.
            host: TCP host to bind on.
            port: TCP port to bind on.
            ws_port: WebSocket port shown in the viewer page.
            refresh_rate: Configured daemon render rate in Hz.
            template_name: Active template name shown in viewer status.
            viewer_bg: Default viewer page background color.
        """
        self._host = host
        self._port = port
        self._frame_holder = _FrameHolder()
        self._app = _build_app(
            store,
            self._frame_holder,
            ws_port,
            refresh_rate,
            template_name,
            viewer_bg,
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the uvicorn HTTP server as a background asyncio task."""
        import uvicorn  # noqa: PLC0415 — deferred to avoid startup cost when unused

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._task = asyncio.create_task(server.serve(), name="casedd-http-server")
        _log.info(
            "HTTP viewer started on http://%s:%d/ — OpenAPI at /docs",
            self._host,
            self._port,
        )

    async def stop(self) -> None:
        """Cancel the HTTP server task."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        _log.info("HTTP server stopped.")

    def set_latest_image(self, image: Image.Image) -> None:
        """Update the latest frame available at ``GET /image``.

        Called by the render loop after each frame is produced.  This method
        is synchronous and safe to call from any thread.

        Args:
            image: Latest rendered PIL Image (RGB mode).
        """
        assert isinstance(image, Image.Image)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=False)
        self._frame_holder.set(buf.getvalue())
