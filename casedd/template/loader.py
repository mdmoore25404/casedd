"""Template loader: parses .casedd YAML files into :class:`~casedd.template.models.Template`.

Public API:
    - :func:`load_template` — load a ``.casedd`` file from disk
    - :class:`TemplateError` — raised for parse/validation errors
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import ValidationError
import yaml

from casedd.template.models import Template

_log = logging.getLogger(__name__)


class TemplateError(Exception):
    """Raised when a .casedd template file cannot be loaded or validated.

    Attributes:
        path: The file path that failed to load.
        reason: Human-readable description of the failure.
    """

    def __init__(self, path: Path, reason: str) -> None:
        """Initialise the error.

        Args:
            path: Template file path.
            reason: Description of the failure.
        """
        super().__init__(f"Template error in '{path}': {reason}")
        self.path = path
        self.reason = reason


def load_template(path: Path) -> Template:
    """Parse a .casedd YAML file into a validated :class:`~casedd.template.models.Template`.

    Args:
        path: Path to the ``.casedd`` file.

    Returns:
        A fully validated :class:`~casedd.template.models.Template` instance.

    Raises:
        TemplateError: If the file is missing, malformed, or fails validation.
    """
    if not path.exists():
        raise TemplateError(path, "file does not exist")
    if not path.is_file():
        raise TemplateError(path, "path is not a file")

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TemplateError(path, f"cannot read file: {exc}") from exc

    try:
        raw = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise TemplateError(path, f"YAML parse error: {exc}") from exc

    if not isinstance(raw, dict):
        raise TemplateError(path, "top-level value must be a YAML mapping")

    try:
        template = Template.model_validate(raw)
    except ValidationError as exc:
        # Summarise all validation errors into one readable message
        errors = "; ".join(
            f"{'.'  .join(str(loc_part) for loc_part in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise TemplateError(path, f"validation failed — {errors}") from exc

    _log.debug("Loaded template '%s' from %s", template.name, path)
    return template
