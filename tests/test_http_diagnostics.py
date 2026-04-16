"""Tests for /api/diagnostics endpoint (issue #46)."""

from __future__ import annotations

from pathlib import Path

import pytest

from casedd.outputs.http_viewer import _walk_widget_sources
from casedd.template.models import WidgetConfig
from tests.conftest import _make_client

# ---------------------------------------------------------------------------
# _walk_widget_sources helper
# ---------------------------------------------------------------------------


def test_walk_sources_simple() -> None:
    """Single-source widget produces one entry."""
    cfg = WidgetConfig.model_validate({"type": "value", "source": "cpu.percent"})
    result = _walk_widget_sources("cpu", cfg)
    assert result == [{"widget": "cpu", "source": "cpu.percent"}]


def test_walk_sources_none() -> None:
    """Widget with no source or sources produces no entries."""
    cfg = WidgetConfig.model_validate({"type": "text", "content": "hello"})
    result = _walk_widget_sources("lbl", cfg)
    assert result == []


def test_walk_sources_multi() -> None:
    """Multi-source sparkline yields one entry per source."""
    cfg = WidgetConfig.model_validate(
        {"type": "sparkline", "sources": ["net.rx", "net.tx"]}
    )
    result = _walk_widget_sources("spark", cfg)
    assert {r["source"] for r in result} == {"net.rx", "net.tx"}
    assert all(r["widget"] == "spark" for r in result)


def test_walk_sources_panel_children() -> None:
    """Sources inside panel direction-children are collected with qualified names."""
    cfg = WidgetConfig.model_validate(
        {
            "type": "panel",
            "children": [
                {"type": "value", "source": "cpu.percent"},
                {"type": "value", "source": "mem.percent"},
            ],
        }
    )
    result = _walk_widget_sources("panel", cfg)
    sources = {r["source"] for r in result}
    assert sources == {"cpu.percent", "mem.percent"}
    # Widget names include the child path
    assert any("children[0]" in r["widget"] for r in result)


def test_walk_sources_panel_children_named() -> None:
    """Sources inside named panel children are collected with qualified names."""
    cfg = WidgetConfig.model_validate(
        {
            "type": "panel",
            "grid": {"template_areas": '"cpu mem"', "columns": "1fr 1fr", "rows": "1fr"},
            "children_named": {
                "cpu": {"type": "value", "source": "cpu.percent"},
                "mem": {"type": "value", "source": "mem.percent"},
            },
        }
    )
    result = _walk_widget_sources("panel", cfg)
    sources = {r["source"] for r in result}
    assert sources == {"cpu.percent", "mem.percent"}


# ---------------------------------------------------------------------------
# /api/diagnostics endpoint
# ---------------------------------------------------------------------------


def _make_health_getter(status: str = "ok", error_msg: str | None = None) -> dict[str, object]:
    """Build a minimal getter health snapshot."""
    return {
        "getters": [
            {
                "name": "CpuGetter",
                "status": status,
                "error_count": 0 if status == "ok" else 3,
                "consecutive_errors": 0,
                "last_error_msg": error_msg,
                "last_error_at": None,
                "last_success_at": 1000.0,
            }
        ]
    }


def test_diagnostics_returns_200() -> None:
    """GET /api/diagnostics returns 200."""
    client, _ = _make_client(
        health_provider=_make_health_getter
    )
    resp = client.get("/api/diagnostics")
    assert resp.status_code == 200


def test_diagnostics_structure() -> None:
    """Response contains top-level 'getters' and 'panels' keys."""
    client, _ = _make_client(
        health_provider=_make_health_getter
    )
    data = client.get("/api/diagnostics").json()
    assert "getters" in data
    assert "panels" in data


def test_diagnostics_getters_forwarded() -> None:
    """Getter health records from health_provider appear in diagnostics."""
    client, _ = _make_client(
        health_provider=lambda: _make_health_getter(status="error", error_msg="timeout")
    )
    data = client.get("/api/diagnostics").json()
    getters = data["getters"]
    assert len(getters) == 1
    assert getters[0]["name"] == "CpuGetter"
    assert getters[0]["status"] == "error"
    assert getters[0]["last_error_msg"] == "timeout"


def test_diagnostics_no_health_provider() -> None:
    """When no health_provider is set, getters list is empty."""
    client, _ = _make_client()
    data = client.get("/api/diagnostics").json()
    assert data["getters"] == []


def test_diagnostics_missing_sources_detected(tmp_path: Path) -> None:
    """Widget sources not in the data store appear in missing_sources."""
    # Write a minimal template referencing a key that is not in the store.
    tpl = tmp_path / "test_diag.casedd"
    tpl.write_text(
        "name: test_diag\n"
        "grid:\n"
        "  template_areas: '\"cpu\"'\n"
        "  columns: 1fr\n"
        "  rows: 1fr\n"
        "widgets:\n"
        "  cpu:\n"
        "    type: value\n"
        "    source: cpu.percent\n",
        encoding="utf-8",
    )

    client, store = _make_client(templates_dir=tmp_path)
    # Set the active template for panel 'main' without populating cpu.percent.
    store.update({"casedd.template.current.main": "test_diag"})

    data = client.get("/api/diagnostics").json()
    panel = next(p for p in data["panels"] if p["name"] == "main")
    missing = panel["missing_sources"]
    assert len(missing) == 1
    assert missing[0]["source"] == "cpu.percent"
    assert "hint" in missing[0]
    assert missing[0]["widget"] == "cpu"


def test_diagnostics_no_missing_when_key_present(tmp_path: Path) -> None:
    """No missing_sources when all widget sources exist in the data store."""
    tpl = tmp_path / "test_diag2.casedd"
    tpl.write_text(
        "name: test_diag2\n"
        "grid:\n"
        "  template_areas: '\"cpu\"'\n"
        "  columns: 1fr\n"
        "  rows: 1fr\n"
        "widgets:\n"
        "  cpu:\n"
        "    type: value\n"
        "    source: cpu.percent\n",
        encoding="utf-8",
    )

    client, store = _make_client(templates_dir=tmp_path)
    store.update({
        "casedd.template.current.main": "test_diag2",
        "cpu.percent": 42.0,
    })

    data = client.get("/api/diagnostics").json()
    panel = next(p for p in data["panels"] if p["name"] == "main")
    assert panel["missing_sources"] == []


def test_diagnostics_missing_sources_deduped(tmp_path: Path) -> None:
    """The same source key referenced by multiple widgets appears only once."""
    tpl = tmp_path / "test_dup.casedd"
    tpl.write_text(
        "name: test_dup\n"
        "grid:\n"
        "  template_areas: '\"a b\"'\n"
        "  columns: 1fr 1fr\n"
        "  rows: 1fr\n"
        "widgets:\n"
        "  a:\n"
        "    type: value\n"
        "    source: cpu.percent\n"
        "  b:\n"
        "    type: bar\n"
        "    source: cpu.percent\n",
        encoding="utf-8",
    )

    client, store = _make_client(templates_dir=tmp_path)
    store.update({"casedd.template.current.main": "test_dup"})

    data = client.get("/api/diagnostics").json()
    panel = next(p for p in data["panels"] if p["name"] == "main")
    sources_seen = [m["source"] for m in panel["missing_sources"]]
    assert sources_seen.count("cpu.percent") == 1


def test_diagnostics_panel_no_active_template() -> None:
    """Panel with no active template has empty missing_sources and no load_error."""
    client, _ = _make_client()
    data = client.get("/api/diagnostics").json()
    panel = next(p for p in data["panels"] if p["name"] == "main")
    assert panel["current_template"] == ""
    assert panel["missing_sources"] == []
    assert panel["load_error"] is None


def test_diagnostics_hint_contains_namespace(tmp_path: Path) -> None:
    """Hint message identifies the source namespace for quick triage."""
    tpl = tmp_path / "test_hint.casedd"
    tpl.write_text(
        "name: test_hint\n"
        "grid:\n"
        "  template_areas: '\"net\"'\n"
        "  columns: 1fr\n"
        "  rows: 1fr\n"
        "widgets:\n"
        "  net:\n"
        "    type: value\n"
        "    source: net.bytes_recv_rate\n",
        encoding="utf-8",
    )

    client, store = _make_client(templates_dir=tmp_path)
    store.update({"casedd.template.current.main": "test_hint"})

    data = client.get("/api/diagnostics").json()
    panel = next(p for p in data["panels"] if p["name"] == "main")
    hint = panel["missing_sources"][0]["hint"]
    # Hint should mention both the full key and the namespace
    assert "net.bytes_recv_rate" in hint
    assert "net" in hint


@pytest.mark.parametrize("status", ["ok", "error", "starting", "inactive"])
def test_diagnostics_all_getter_statuses_forwarded(status: str) -> None:
    """All getter status variants are forwarded without modification."""
    client, _ = _make_client(
        health_provider=lambda: {"getters": [{"name": "X", "status": status}]}
    )
    data = client.get("/api/diagnostics").json()
    assert data["getters"][0]["status"] == status
