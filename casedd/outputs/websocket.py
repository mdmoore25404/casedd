"""WebSocket output: broadcasts rendered frames to connected browser clients.

Runs a FastAPI WebSocket endpoint that pushes every rendered frame as a
JPEG-encoded image wrapped in a JSON envelope.  Multiple simultaneous clients
are supported.  Clients that disconnect mid-send are removed silently.

The server is started via :func:`start_server` and stopped via
:func:`stop_server`.  Call :func:`broadcast` once per render tick from the
main event loop.

Public API:
    - :func:`start_server` — start the uvicorn WebSocket server task
    - :func:`stop_server` — cancel the server task
    - :func:`broadcast` — send a frame to all connected WS clients
    - :class:`WebSocketOutput` — thin wrapper holding server state
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Generator
from contextlib import contextmanager, suppress
import io
import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from PIL import Image

from casedd.outputs.base import OutputBackend

if TYPE_CHECKING:
    from casedd.config import OutputBackendConfig

_log = logging.getLogger(__name__)


class _ConnectionManager:
    """Tracks active WebSocket connections and broadcasts messages.

    Thread-safety: all methods must be called from the same event loop.
    """

    def __init__(self) -> None:
        """Initialise with an empty connection set."""
        self._connections: set[WebSocket] = set()

    @contextmanager
    def _registered(self, ws: WebSocket) -> Generator[None, None, None]:
        """Context manager that registers / unregisters a WebSocket.

        Args:
            ws: The WebSocket connection to track.

        Yields:
            None
        """
        self._connections.add(ws)
        try:
            yield
        finally:
            self._connections.discard(ws)

    async def handle(self, ws: WebSocket) -> None:
        """Accept a new WebSocket connection and keep it alive until close.

        Clients are expected to be passive receivers; incoming messages are
        silently discarded.

        Args:
            ws: The incoming WebSocket connection.
        """
        await ws.accept()
        _log.info("WebSocket client connected: %s", ws.client)
        with self._registered(ws):
            try:
                while True:
                    # Keep the connection open; we don't use incoming data
                    await ws.receive_text()
            except WebSocketDisconnect:
                _log.info("WebSocket client disconnected: %s", ws.client)

    async def broadcast(self, data: str) -> None:
        """Send a JSON string to all connected clients.

        Clients that raise an error during send are silently removed.

        Args:
            data: JSON-encoded string to broadcast.
        """
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        self._connections -= dead

    @property
    def client_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self._connections)


def _build_app(manager: _ConnectionManager) -> FastAPI:
    """Construct the FastAPI app exposing the /ws endpoint.

    Args:
        manager: Shared connection manager instance.

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(title="CASEDD WebSocket", docs_url=None, redoc_url=None)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await manager.handle(websocket)

    return app


class WebSocketOutput(OutputBackend):
    """Manages the WebSocket broadcast server.

    Implements :class:`~casedd.outputs.base.OutputBackend` so it can be
    registered with the pluggable backend system.

    Args:
        host: Bind host for the uvicorn server.
        port: Bind port for the uvicorn server.
    """

    def __init__(self, host: str, port: int) -> None:
        """Initialise the WebSocket output.

        Args:
            host: TCP host to bind uvicorn on.
            port: TCP port to bind uvicorn on.
        """
        self._host = host
        self._port = port
        self._manager = _ConnectionManager()
        self._app = _build_app(self._manager)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the uvicorn WebSocket server as a background asyncio task."""
        import uvicorn  # noqa: PLC0415 — deferred to avoid startup cost when unused

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
            # Disable WebSocket per-message DEFLATE compression.  The frames are
            # JPEG-encoded and already compressed, so deflate achieves little
            # while consuming significant CPU scanning the payload.
            ws_per_message_deflate=False,
        )
        server = uvicorn.Server(config)
        self._task = asyncio.create_task(server.serve(), name="casedd-ws-server")
        _log.info("WebSocket server started on ws://%s:%d/ws", self._host, self._port)

    async def stop(self) -> None:
        """Cancel the WebSocket server task."""
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        _log.info("WebSocket server stopped.")

    async def broadcast(self, image: Image.Image, panel: str | None = None) -> None:
        """Encode a PIL image as JPEG and broadcast it to all WS clients.

        Skips the encode/broadcast step when no clients are connected to avoid
        unnecessary CPU/memory work.

        Args:
            image: The rendered frame to broadcast.
            panel: Optional panel identifier associated with this frame.
        """
        if self._manager.client_count == 0:
            return

        buf = io.BytesIO()
        # JPEG encodes ~5-10x faster than PNG for rendered content and the
        # compressed payload resists WS deflate better (we disable deflate
        # above, but JPEG stays smaller regardless).
        image.save(buf, format="JPEG", quality=80, optimize=False)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        if panel is None:
            payload = f'{{"type":"frame","format":"jpeg","data":"{encoded}"}}'
        else:
            payload = f'{{"type":"frame","format":"jpeg","panel":"{panel}","data":"{encoded}"}}'
        await self._manager.broadcast(payload)

    # ------------------------------------------------------------------
    # OutputBackend interface
    # ------------------------------------------------------------------

    async def output(
        self,
        image: Image.Image,
        config: OutputBackendConfig | None = None,  # noqa: ARG002  # interface compat
    ) -> None:
        """Broadcast one rendered frame to all connected WebSocket clients.

        Delegates to :meth:`broadcast` without a panel tag.  The ``config``
        parameter is accepted for interface compatibility but is not used here
        — port and host are fixed at construction time.

        Args:
            image: Rendered ``PIL.Image.Image`` in ``RGB`` mode, pre-scaled
                to this backend's declared resolution by the dispatch layer.
            config: Unused; present for :class:`OutputBackend` compatibility.
        """
        await self.broadcast(image)

    def is_healthy(self) -> bool:
        """Return ``True`` when the WebSocket server task is running.

        Returns:
            ``True`` if the background server task exists and has not failed;
            ``False`` when the server has not been started or has crashed.
        """
        return self._task is not None and not self._task.done()

    @property
    def client_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return self._manager.client_count

    def get_config(self) -> dict[str, object]:
        """Return a snapshot of the WebSocket backend's configuration.

        Returns:
            Mapping with ``host``, ``port``, and ``client_count`` entries.
        """
        return {
            "host": self._host,
            "port": self._port,
            "client_count": self._manager.client_count,
        }
