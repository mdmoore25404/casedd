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
import logging
import threading
from typing import Annotated

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
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
    body {{ margin: 0; background: #111; display: flex;
            justify-content: center; align-items: center; height: 100vh; }}
    img  {{ max-width: 100%; max-height: 100%; image-rendering: pixelated; }}
    #status {{ position: fixed; top: 6px; right: 10px;
               font: 12px monospace; color: #666; }}
  </style>
</head>
<body>
  <img id="frame" src="/image" alt="CASEDD frame" />
  <div id="status">connecting…</div>
  <script>
    const img    = document.getElementById('frame');
    const status = document.getElementById('status');
    let   ws, reconnect;

    function connect() {{
      ws = new WebSocket('ws://' + location.hostname + ':{ws_port}/ws');
      ws.onopen  = () => {{ status.textContent = 'live'; }};
      ws.onclose = () => {{
        status.textContent = 'reconnecting…';
        reconnect = setTimeout(connect, 2000);
      }};
      ws.onerror = () => ws.close();
      ws.onmessage = (ev) => {{
        const msg = JSON.parse(ev.data);
        if (msg.type === 'frame') {{
          img.src = 'data:image/png;base64,' + msg.data;
        }}
      }};
    }}

    connect();
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------


def _build_app(store: DataStore, frame_holder: _FrameHolder, ws_port: int) -> FastAPI:
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
        return _VIEWER_HTML.format(ws_port=ws_port)

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

    def __init__(
        self,
        store: DataStore,
        host: str,
        port: int,
        ws_port: int,
    ) -> None:
        """Initialise the HTTP viewer output.

        Args:
            store: Shared data store.
            host: TCP host to bind on.
            port: TCP port to bind on.
            ws_port: WebSocket port shown in the viewer page.
        """
        self._host = host
        self._port = port
        self._frame_holder = _FrameHolder()
        self._app = _build_app(store, self._frame_holder, ws_port)
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

    def set_latest_image(self, image: Image) -> None:  # type: ignore[name-defined]  # noqa: F821
        """Update the latest frame available at ``GET /image``.

        Called by the render loop after each frame is produced.  This method
        is synchronous and safe to call from any thread.

        Args:
            image: Latest rendered PIL Image (RGB mode).
        """
        from PIL import Image as _Image  # noqa: PLC0415 — inner import only for type hint

        assert isinstance(image, _Image.Image)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=False)
        self._frame_holder.set(buf.getvalue())
