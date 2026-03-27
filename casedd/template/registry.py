"""Template registry: maps template names to loaded Template objects.

Watches for file changes and hot-reloads templates when their ``.casedd``
file is modified, so a running daemon picks up edits without a restart.

Public API:
    - :class:`TemplateRegistry` — holds and reloads all templates
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
import threading

from watchfiles import Change, awatch

from casedd.template.loader import TemplateError, load_template
from casedd.template.models import Template

_log = logging.getLogger(__name__)


class TemplateRegistry:
    """Registry of loaded .casedd templates with transparent hot-reload.

    On each call to :meth:`get`, if the backing file has been modified since
    it was last loaded, the template is silently reloaded. The previous
    (valid) template is retained if the new file fails to parse.

    Args:
        templates_dir: Directory containing ``.casedd`` template files.
    """

    def __init__(self, templates_dir: Path) -> None:
        """Initialise the registry.

        Args:
            templates_dir: Path to the directory holding ``.casedd`` files.
        """
        self._dir = templates_dir
        self._cache: dict[str, Template] = {}
        self._mtimes: dict[str, float] = {}
        self._lock = threading.Lock()
        self._dirty: set[str] = set()
        self._watch_task: asyncio.Task[None] | None = None
        self._watching = False

    async def start(self) -> None:
        """Start background file watching for template change events."""
        if self._watch_task is not None:
            return
        self._watching = True
        self._watch_task = asyncio.create_task(self._watch_loop(), name="template-watch")

    async def stop(self) -> None:
        """Stop background file watching."""
        self._watching = False
        if self._watch_task is None:
            return
        self._watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._watch_task
        self._watch_task = None

    def get(self, name: str) -> Template:
        """Return the named template, reloading from disk if modified.

        Args:
            name: Template name (filename without ``.casedd`` extension).

        Returns:
            The loaded :class:`~casedd.template.models.Template`.

        Raises:
            TemplateError: If the template file cannot be found or parsed and
                no valid cached version exists.
        """
        path = self._dir / f"{name}.casedd"

        should_reload = False
        with self._lock:
            if name not in self._cache:
                should_reload = True
            elif name in self._dirty:
                should_reload = True
                self._dirty.discard(name)

        if should_reload:
            _log.info("(Re)loading template '%s' from %s", name, path)
            try:
                template = load_template(path)
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    mtime = 0.0
                with self._lock:
                    self._cache[name] = template
                    self._mtimes[name] = mtime
            except TemplateError as exc:
                with self._lock:
                    has_cached = name in self._cache
                if has_cached:
                    _log.error(
                        "Failed to reload template '%s': %s — keeping previous version.",
                        name, exc,
                    )
                else:
                    raise
        with self._lock:
            return self._cache[name]

    def preload_all(self) -> None:
        """Attempt to load all .casedd files in the templates directory.

        Errors are logged but do not prevent other templates from loading.
        This is called at daemon startup for early validation feedback.
        """
        if not self._dir.is_dir():
            _log.warning("Templates directory '%s' does not exist.", self._dir)
            return

        for path in sorted(self._dir.glob("*.casedd")):
            name = path.stem
            try:
                self.get(name)
                _log.info("Pre-loaded template: %s", name)
            except TemplateError as exc:
                _log.error("Could not pre-load template '%s': %s", name, exc)

    async def _watch_loop(self) -> None:
        """Watch template directory and mark changed templates dirty."""
        if not self._dir.is_dir():
            _log.warning("Templates directory '%s' does not exist for watch mode.", self._dir)
            return

        try:
            async for changes in awatch(self._dir):
                if not self._watching:
                    return
                dirty: set[str] = set()
                for change, raw_path in changes:
                    path = Path(raw_path)
                    if path.suffix != ".casedd":
                        continue
                    if change in {Change.added, Change.modified, Change.deleted}:
                        dirty.add(path.stem)
                if dirty:
                    with self._lock:
                        self._dirty.update(dirty)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("Template file watcher failed; templates will reload on restart only.")
