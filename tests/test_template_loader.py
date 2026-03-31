"""Tests for :mod:`casedd.template.loader` and template validation (issue #30)."""

from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from casedd.template.loader import TemplateError, load_template
from casedd.template.models import Template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_VALID = textwrap.dedent("""\
    name: test_template
    grid:
      template_areas: |
        widget_a
      columns: 1fr
      rows: 1fr
    widgets:
      widget_a:
        type: text
        content: hello
""")


def _write(tmp_path: Path, content: str, name: str = "t.casedd") -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Happy-path loading
# ---------------------------------------------------------------------------


def test_load_minimal_valid_template(tmp_path: Path) -> None:
    """load_template() returns a Template for a minimal valid .casedd file."""
    path = _write(tmp_path, _MINIMAL_VALID)
    tmpl = load_template(path)
    assert isinstance(tmpl, Template)
    assert tmpl.name == "test_template"


def test_load_template_widget_count(tmp_path: Path) -> None:
    """Widget dict has the expected number of entries."""
    path = _write(tmp_path, _MINIMAL_VALID)
    tmpl = load_template(path)
    assert len(tmpl.widgets) == 1
    assert "widget_a" in tmpl.widgets


def test_load_from_real_template_file() -> None:
    """system_stats.casedd in the templates/ directory loads without error."""
    real = Path("templates/system_stats.casedd")
    if not real.exists():
        pytest.skip("templates/system_stats.casedd not present")
    tmpl = load_template(real)
    assert tmpl.name == "system_stats"


# ---------------------------------------------------------------------------
# File-not-found errors
# ---------------------------------------------------------------------------


def test_missing_file_raises_template_error(tmp_path: Path) -> None:
    """load_template() raises TemplateError for a non-existent file."""
    with pytest.raises(TemplateError, match="does not exist"):
        load_template(tmp_path / "nonexistent.casedd")


def test_directory_raises_template_error(tmp_path: Path) -> None:
    """load_template() raises TemplateError when path is a directory."""
    with pytest.raises(TemplateError, match="not a file"):
        load_template(tmp_path)


# ---------------------------------------------------------------------------
# YAML parse errors
# ---------------------------------------------------------------------------


def test_invalid_yaml_raises_template_error(tmp_path: Path) -> None:
    """Malformed YAML raises TemplateError with a parse-error message."""
    path = _write(tmp_path, "name: [\ncorrupt yaml")
    with pytest.raises(TemplateError, match="YAML parse error"):
        load_template(path)


def test_non_mapping_yaml_raises_template_error(tmp_path: Path) -> None:
    """A YAML file whose top-level is a list raises TemplateError."""
    path = _write(tmp_path, "- foo\n- bar\n")
    with pytest.raises(TemplateError, match="YAML mapping"):
        load_template(path)


# ---------------------------------------------------------------------------
# Validation errors (issue #30 — improved messages)
# ---------------------------------------------------------------------------


def test_missing_name_raises_validation_error(tmp_path: Path) -> None:
    """Template without 'name' raises TemplateError with validation message."""
    content = textwrap.dedent("""\
        grid:
          template_areas: |
            w
          columns: 1fr
          rows: 1fr
        widgets:
          w:
            type: text
            content: hi
    """)
    path = _write(tmp_path, content)
    with pytest.raises(TemplateError, match="validation error"):
        load_template(path)


def test_unknown_widget_type_raises_validation_error(tmp_path: Path) -> None:
    """An unknown widget type raises TemplateError."""
    content = _MINIMAL_VALID.replace("type: text", "type: nonexistent_widget")
    path = _write(tmp_path, content)
    with pytest.raises(TemplateError, match="validation error"):
        load_template(path)


def test_validation_error_includes_field_path(tmp_path: Path) -> None:
    """The TemplateError.reason includes the dotted field path of the failure."""
    # Use an invalid value for the refresh_rate field (must be > 0)
    content = _MINIMAL_VALID.rstrip() + "\nrefresh_rate: -1\n"
    path = _write(tmp_path, content)
    with pytest.raises(TemplateError) as exc_info:
        load_template(path)
    assert "refresh_rate" in str(exc_info.value)


def test_multiple_validation_errors_reported(tmp_path: Path) -> None:
    """When multiple fields are invalid, the count appears in the message."""
    # Missing name AND invalid refresh_rate
    content = textwrap.dedent("""\
        refresh_rate: -5
        grid:
          template_areas: |
            w
          columns: 1fr
          rows: 1fr
        widgets:
          w:
            type: text
            content: hi
    """)
    path = _write(tmp_path, content)
    with pytest.raises(TemplateError) as exc_info:
        load_template(path)
    # Message should contain count > 1
    assert "2 validation errors" in str(exc_info.value) or "validation error" in str(
        exc_info.value
    )


# ---------------------------------------------------------------------------
# TemplateError convenience
# ---------------------------------------------------------------------------


def test_template_error_stores_path_and_reason() -> None:
    """TemplateError exposes .path and .reason attributes."""
    p = Path("/some/template.casedd")
    err = TemplateError(p, "bad field")
    assert err.path == p
    assert err.reason == "bad field"
    assert "bad field" in str(err)
