"""Tests for viewer_layout config model, startup validation, and /api/panels exposure.

Covers:
- ViewerLayout Pydantic model validation
- Config.viewer_layout parsing from dict
- _validate_config_pre_run: unknown panels in cells raises ValueError
- _validate_config_pre_run: missing templates emit CRITICAL log (don't raise)
- /api/panels includes viewer_layout in the response
- /api/panels returns null viewer_layout when not configured
- TemplateRegistry.available_template_names() returns file stems
"""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from casedd.config import Config, PanelConfig, ViewerLayout
from casedd.daemon import Daemon
from casedd.data_store import DataStore
import casedd.outputs.http_viewer as _http_viewer_mod
from casedd.outputs.http_viewer import HttpViewerOutput
from casedd.template.registry import TemplateRegistry

# -- ViewerLayout model --


def test_viewer_layout_default_columns() -> None:
    """Default columns is 2."""
    layout = ViewerLayout(cells=["primary", "virtpanel"])
    assert layout.columns == 2


def test_viewer_layout_empty_cells_allowed() -> None:
    """Empty cells list is valid (no cells configured yet)."""
    layout = ViewerLayout(columns=1, cells=[])
    assert layout.cells == []


def test_viewer_layout_blank_cell_string_allowed() -> None:
    """Empty string cells are valid placeholder entries."""
    layout = ViewerLayout(columns=2, cells=["primary", "", "virtpanel", ""])
    assert layout.cells[1] == ""


def test_viewer_layout_columns_range() -> None:
    """columns must be 1-16."""
    with pytest.raises(ValidationError):
        ViewerLayout(columns=0, cells=[])
    with pytest.raises(ValidationError):
        ViewerLayout(columns=17, cells=[])


def test_viewer_layout_roundtrip_in_config() -> None:
    """ViewerLayout survives Config construction and model_dump round-trip."""
    cfg = Config(
        panels=[PanelConfig(name="primary"), PanelConfig(name="virt")],
        viewer_layout=ViewerLayout(columns=2, cells=["primary", "virt"]),
    )
    assert cfg.viewer_layout is not None
    assert cfg.viewer_layout.columns == 2
    assert cfg.viewer_layout.cells == ["primary", "virt"]
    dumped = cfg.viewer_layout.model_dump()
    assert dumped == {"columns": 2, "cells": ["primary", "virt"]}


# -- Startup validation: _validate_config_pre_run --


class _FakeRegistry:
    """Minimal registry double with a fixed set of available templates."""

    def __init__(self, available: set[str]) -> None:
        self._available = available

    def available_template_names(self) -> set[str]:
        """Return the pre-configured set of available templates."""
        return set(self._available)


def _make_daemon_with_layout(
    panels: list[PanelConfig],
    layout: ViewerLayout | None,
    available_templates: set[str],
) -> Daemon:
    """Build a Daemon instance wired with the given panels and layout."""
    cfg = Config(
        template="system_stats",
        panels=panels,
        viewer_layout=layout,
    )
    return Daemon(cfg)


def test_validate_unknown_panel_in_cells_raises() -> None:
    """viewer_layout.cells referencing an unknown panel name raises ValueError."""
    daemon = _make_daemon_with_layout(
        panels=[PanelConfig(name="primary")],
        layout=ViewerLayout(columns=2, cells=["primary", "ghost_panel"]),
        available_templates={"system_stats"},
    )
    registry = _FakeRegistry({"system_stats"})
    # Patch the daemon's registry reference for the validation call.
    daemon._cfg = Config(  # type: ignore[misc]
        template="system_stats",
        panels=[PanelConfig(name="primary")],
        viewer_layout=ViewerLayout(columns=2, cells=["primary", "ghost_panel"]),
    )
    with pytest.raises(ValueError, match="ghost_panel"):
        # Access the private method because it is a startup check.
        daemon._validate_config_pre_run(registry)  # type: ignore[arg-type]


def test_validate_blank_cell_does_not_raise() -> None:
    """Empty string cells are valid placeholders and must not trigger validation errors."""
    cfg = Config(
        template="system_stats",
        panels=[PanelConfig(name="primary"), PanelConfig(name="virt")],
        viewer_layout=ViewerLayout(columns=2, cells=["primary", "virt", "", ""]),
    )
    daemon = Daemon(cfg)
    registry = _FakeRegistry({"system_stats"})
    # Should pass without raising.
    daemon._validate_config_pre_run(registry)  # type: ignore[arg-type]


def test_validate_no_layout_passes() -> None:
    """No viewer_layout configured — validation always passes."""
    cfg = Config(
        template="system_stats",
        panels=[PanelConfig(name="primary")],
    )
    daemon = Daemon(cfg)
    registry = _FakeRegistry({"system_stats"})
    daemon._validate_config_pre_run(registry)  # type: ignore[arg-type]


def test_validate_missing_template_logs_critical(caplog: pytest.LogCaptureFixture) -> None:
    """A missing template emits a CRITICAL log message but does not raise."""
    cfg = Config(
        template="system_stats",
        panels=[PanelConfig(name="primary", template="nonexistent_template")],
    )
    daemon = Daemon(cfg)
    registry = _FakeRegistry({"system_stats"})  # nonexistent_template not in set
    with caplog.at_level(logging.CRITICAL, logger="casedd.daemon"):
        daemon._validate_config_pre_run(registry)  # type: ignore[arg-type]
    assert any("nonexistent_template" in r.message for r in caplog.records)


# -- API: /api/panels viewer_layout field --


def _make_test_client(
    panels: list[dict[str, object]],
    viewer_layout: dict[str, object] | None,
) -> TestClient:
    """Build a TestClient with the given panels and optional viewer_layout."""
    store = DataStore()
    output = HttpViewerOutput(
        store=store,
        host="127.0.0.1",
        port=0,
        ws_port=8765,
        panels=panels,
        default_panel=str(panels[0]["name"]) if panels else "primary",
        viewer_bg="#111111",
        templates_dir=Path("templates"),
        history_provider=dict,
        viewer_layout=viewer_layout,
    )
    return TestClient(output._app, raise_server_exceptions=False)


def test_api_panels_includes_viewer_layout() -> None:
    """/api/panels returns viewer_layout when configured."""
    panels = [
        {"name": "primary", "display_name": "Primary"},
        {"name": "virt", "display_name": "Virtual"},
    ]
    layout = {"columns": 2, "cells": ["primary", "virt"]}
    client = _make_test_client(panels, layout)
    resp = client.get("/api/panels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer_layout"] == layout


def test_api_panels_viewer_layout_null_when_not_configured() -> None:
    """/api/panels returns null viewer_layout when no layout is configured."""
    panels = [{"name": "primary", "display_name": "Primary"}]
    client = _make_test_client(panels, None)
    resp = client.get("/api/panels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["viewer_layout"] is None


# -- TemplateRegistry: available_template_names --


def test_available_template_names_returns_stems() -> None:
    """available_template_names() returns the stems of .casedd files on disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tdir = Path(tmpdir)
        (tdir / "system_stats.casedd").touch()
        (tdir / "apod.casedd").touch()
        (tdir / "README.md").touch()  # non-.casedd file; must be ignored
        registry = TemplateRegistry(tdir)
        names = registry.available_template_names()
    assert names == {"system_stats", "apod"}


def test_available_template_names_empty_dir() -> None:
    """available_template_names() returns empty set for an empty directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        registry = TemplateRegistry(Path(tmpdir))
        assert registry.available_template_names() == set()


def test_available_template_names_missing_dir() -> None:
    """available_template_names() returns empty set when directory doesn't exist."""
    registry = TemplateRegistry(Path("/tmp/casedd-nonexistent-dir-xyz"))
    assert registry.available_template_names() == set()


# -- Panel-name overlay: hover-reveal + kiosk hiding --


def _get_viewer_html() -> str:
    """Return the raw _LIGHT_VIEWER_HTML string from the http_viewer module."""
    return _http_viewer_mod._LIGHT_VIEWER_HTML


def test_panel_tile_name_hidden_by_default() -> None:
    """panel-tile-name must have opacity:0 so it is invisible by default."""
    html = _get_viewer_html()
    assert "opacity: 0" in html or "opacity:0" in html


def test_panel_tile_name_visible_on_hover() -> None:
    """Hovering .panel-tile must reveal the label via opacity:1."""
    html = _get_viewer_html()
    assert ".panel-tile:hover .panel-tile-name" in html
    # The hover rule must set opacity
    hover_block_start = html.index(".panel-tile:hover .panel-tile-name")
    # Grab a short slice after the selector to find the opacity rule.
    snippet = html[hover_block_start : hover_block_start + 80]
    assert "opacity" in snippet


def test_panel_tile_name_hidden_in_kiosk() -> None:
    """In kiosk mode the label must be completely suppressed (display:none)."""
    html = _get_viewer_html()
    # Must have a kiosk-scoped rule that hides the overlay entirely.
    assert "body.kiosk .panel-tile-name" in html
    kiosk_idx = html.index("body.kiosk .panel-tile-name")
    snippet = html[kiosk_idx : kiosk_idx + 60]
    assert "none" in snippet  # display: none
