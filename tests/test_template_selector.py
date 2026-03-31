"""Tests for template rotation behavior in :mod:`casedd.template.selector`."""

from __future__ import annotations

from casedd.template.selector import TemplateSelector


def test_rotation_entries_exclude_base_when_rotation_configured() -> None:
    """Configured rotation templates are used as-is without auto-prepending base."""
    selector = TemplateSelector(
        base_template="system_stats",
        rotation_templates=["apod", "nzbget_queue"],
        rotation_interval=30.0,
        rotation_enabled=True,
        schedule_rules=[],
        trigger_rules=[],
    )

    templates = [entry.template for entry in selector.rotation_entries]
    assert templates == ["apod", "nzbget_queue"]


def test_rotation_disabled_falls_back_to_base_template() -> None:
    """When rotation is disabled, selector returns base template."""
    selector = TemplateSelector(
        base_template="system_stats",
        rotation_templates=["apod", "nzbget_queue"],
        rotation_interval=30.0,
        rotation_enabled=False,
        schedule_rules=[],
        trigger_rules=[],
    )

    chosen = selector.select_template({})
    assert chosen == "system_stats"
