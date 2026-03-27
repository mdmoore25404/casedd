"""Template registry: maps template names to loaded Template objects.

Watches for file changes and hot-reloads templates when their ``.casedd``
file is modified, so a running daemon picks up edits without a restart.

Public API:
    - :class:`TemplateRegistry` — holds and reloads all templates
"""

from __future__ import annotations

import logging
from pathlib import Path

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

        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            if name in self._cache:
                _log.warning(
                    "Template file '%s' inaccessible (%s) — using cached version.", path, exc
                )
                return self._cache[name]
            raise TemplateError(path, f"file not found: {exc}") from exc

        if name not in self._cache or self._mtimes.get(name) != mtime:
            _log.info("(Re)loading template '%s' from %s", name, path)
            try:
                template = load_template(path)
                self._cache[name] = template
                self._mtimes[name] = mtime
            except TemplateError as exc:
                if name in self._cache:
                    _log.error(
                        "Failed to reload template '%s': %s — keeping previous version.",
                        name, exc,
                    )
                else:
                    raise

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
