"""Tests for getter planning across template rotation.

Issue #77 reported apparent data gaps when rotating between templates.
These tests lock in that getter requirements include non-visible templates in
rotation so pollers remain active while display selection changes.
"""

from __future__ import annotations

from types import SimpleNamespace

from casedd.config import Config, RotationEntry
from casedd.daemon import Daemon


class _FakeRegistry:
    """Minimal template registry double for getter planning tests."""

    def get(self, name: str) -> str:
        """Return the template name itself as a token object."""
        return name


def test_needed_getters_include_rotation_templates() -> None:
    """Getters for rotated templates remain needed even when off-screen."""
    daemon = Daemon(Config())
    source_map: dict[str, set[str]] = {
        "system_stats": {"cpu.percent"},
        "apod": {"apod.title"},
        "nzbget_queue": {"nzbget.queue.total"},
    }
    daemon._template_sources = lambda template: source_map.get(template, set())  # type: ignore[method-assign]

    panel = SimpleNamespace(
        name="primary",
        base_template="system_stats",
        rotation_entries=[
            RotationEntry(template="apod", seconds=10.0, skip_if=[]),
            RotationEntry(template="nzbget_queue", seconds=15.0, skip_if=[]),
        ],
        schedule_rules=[],
        trigger_rules=[],
    )

    needed = daemon._needed_getter_names(
        _FakeRegistry(),
        [panel],
        active_templates={"apod"},
    )

    assert "CpuGetter" in needed
    assert "ApodGetter" in needed
    assert "NZBGetGetter" in needed


def test_needed_getters_include_forced_template() -> None:
    """Force-selected template contributes getter requirements."""
    daemon = Daemon(Config())
    source_map: dict[str, set[str]] = {
        "system_stats": {"cpu.percent"},
        "plex_dashboard": {"plex.active.count"},
    }
    daemon._template_sources = lambda template: source_map.get(template, set())  # type: ignore[method-assign]

    panel = SimpleNamespace(
        name="primary",
        base_template="system_stats",
        rotation_entries=[],
        schedule_rules=[],
        trigger_rules=[],
    )
    daemon._store.set("casedd.template.force.primary", "plex_dashboard")

    needed = daemon._needed_getter_names(
        _FakeRegistry(),
        [panel],
        active_templates={"system_stats"},
    )

    assert "CpuGetter" in needed
    assert "PlexGetter" in needed


def test_getter_name_for_source_includes_pihole() -> None:
    """Pi-hole namespace should resolve to PiHoleGetter."""
    assert Daemon._getter_name_for_source("pihole.queries.total") == "PiHoleGetter"


def test_getter_name_for_source_includes_servarr_namespaces() -> None:
    """Servarr namespaces should resolve to their concrete getter names."""
    assert Daemon._getter_name_for_source("radarr.queue.total") == "RadarrGetter"
    assert Daemon._getter_name_for_source("sonarr.queue.total") == "SonarrGetter"
    assert Daemon._getter_name_for_source("servarr.queue.total") == "ServarrAggregateGetter"


def test_getter_name_for_source_includes_invokeai() -> None:
    """InvokeAI namespace should resolve to InvokeAIGetter."""
    assert Daemon._getter_name_for_source("invokeai.queue.pending_count") == "InvokeAIGetter"
