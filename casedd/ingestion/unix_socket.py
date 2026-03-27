"""Unix domain socket ingestion: accepts JSON data updates from local producers.

Listens on a configurable Unix socket path for newline-delimited JSON messages
of the form::

    {"update": {"cpu.temperature": 72.5, "disk.percent": 38.0}}

Each key/value pair is written to the shared :class:`~casedd.data_store.DataStore`.
Malformed JSON and unknown fields are logged and discarded without crashing.

The listener runs as a persistent asyncio task and supports graceful shutdown
via task cancellation.

Public API:
    - :class:`UnixSocketIngestion` — async listener task wrapper
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path

from casedd.data_store import DataStore

_log = logging.getLogger(__name__)

# Maximum bytes accepted per message to prevent memory exhaustion
_MAX_MESSAGE_BYTES = 65_536


class UnixSocketIngestion:
    """Listens on a Unix domain socket and writes data to the store.

    Args:
        socket_path: Filesystem path for the Unix socket file.
        store: The shared data store to write incoming values into.
    """

    def __init__(self, socket_path: Path, store: DataStore) -> None:
        """Initialise the Unix socket ingestion listener.

        Args:
            socket_path: Path where the socket file will be created.
            store: Shared data store for incoming key/value pairs.
        """
        self._socket_path = socket_path
        self._store = store
        self._task: asyncio.Task[None] | None = None
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Create the socket file and begin accepting connections."""
        # Remove stale socket from a previous run
        if self._socket_path.exists():
            self._socket_path.unlink()

        # Ensure parent directory exists
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
        )
        self._task = asyncio.create_task(
            self._server.serve_forever(),
            name="casedd-unix-socket",
        )
        _log.info("Unix socket ingestion listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Close the socket and cancel the listening task."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        # Clean up the socket file
        if self._socket_path.exists():
            self._socket_path.unlink(missing_ok=True)
        _log.info("Unix socket ingestion stopped.")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection.

        Reads newline-delimited JSON messages until the connection closes.

        Args:
            reader: Async stream reader for the client connection.
            writer: Async stream writer for the client connection (used to close).
        """
        peer = writer.get_extra_info("peername", default="<unknown>")
        _log.debug("Unix socket: client connected (%s)", peer)
        try:
            while True:
                try:
                    line = await reader.readline()
                except asyncio.IncompleteReadError:
                    break

                if not line:
                    break

                if len(line) > _MAX_MESSAGE_BYTES:
                    _log.warning(
                        "Unix socket: message from %s exceeds %d bytes — discarded.",
                        peer,
                        _MAX_MESSAGE_BYTES,
                    )
                    continue

                self._process_line(line, peer)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            _log.debug("Unix socket: client disconnected (%s)", peer)

    def _process_line(self, line: bytes, peer: object) -> None:
        """Parse one JSON line and write values to the data store.

        Args:
            line: Raw bytes from the socket (expected UTF-8 JSON + newline).
            peer: Peer identifier for log messages.
        """
        try:
            payload = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            _log.warning("Unix socket: invalid JSON from %s: %s", peer, exc)
            return

        if not isinstance(payload, dict):
            _log.warning("Unix socket: expected JSON object from %s, got %s.", peer, type(payload))
            return

        update = payload.get("update")
        if not isinstance(update, dict):
            _log.warning("Unix socket: missing 'update' dict from %s.", peer)
            return

        # Filter to only valid store value types; discard any nested objects
        clean: dict[str, float | int | str] = {}
        for key, val in update.items():
            if isinstance(key, str) and isinstance(val, float | int | str):
                clean[key] = val
            else:
                _log.debug(
                    "Unix socket: skipping key %r (value type %s).", key, type(val).__name__
                )

        if clean:
            self._store.update(clean)
            _log.debug("Unix socket: wrote %d key(s) from %s.", len(clean), peer)
