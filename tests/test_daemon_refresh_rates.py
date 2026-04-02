"""Tests for daemon per-template refresh-rate resolution and logging."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from casedd.config import Config
from casedd.daemon import Daemon
from casedd.data_store import DataStore
from casedd.getters.base import BaseGetter
from casedd.template.models import GridConfig, Template, WidgetConfig, WidgetType


class _DummyGetter(BaseGetter):
    """Minimal getter double with a configurable interval."""

    async def fetch(self) -> dict[str, float]:
        """Return an empty payload for scheduler tests."""
        return {}


class _DummyRegistry:
    """Minimal template registry double for refresh-profile tests."""

    def __init__(self, template: Template) -> None:
        self._template = template

    def get(self, name: str) -> Template:
        """Return the configured template regardless of requested name."""
        assert name == self._template.name
        return self._template


def _build_template(
    name: str,
    source: str | None,
    refresh_rate_hz: float | None,
) -> Template:
    """Build a minimal template for refresh-profile tests."""
    widget = (
        WidgetConfig(type=WidgetType.VALUE, source=source)
        if source is not None
        else WidgetConfig(type=WidgetType.TEXT, content="static")
    )
    return Template(
        name=name,
        refresh_rate_hz=refresh_rate_hz,
        grid=GridConfig(template_areas="widget", columns="1fr", rows="1fr"),
        widgets={"widget": widget},
    )


def test_template_refresh_profile_caps_to_fastest_relevant_getter() -> None:
    """Requested template refresh should be capped by the fastest relevant getter."""
    daemon = Daemon(Config(refresh_rate=2.0))
    template = _build_template("system_stats", "cpu.percent", 10.0)
    registry = _DummyRegistry(template)
    getters_by_name = {
        "CpuGetter": _DummyGetter(DataStore(), interval=2.0),
        "WeatherGetter": _DummyGetter(DataStore(), interval=300.0),
    }

    profile = daemon._template_refresh_profile(registry, "system_stats", getters_by_name)

    assert profile.requested_hz == 10.0
    assert profile.getter_cap_hz == 0.5
    assert profile.effective_hz == 0.5
    assert profile.limiting_getters == ("CpuGetter",)


def test_template_refresh_profile_falls_back_to_global_rate() -> None:
    """Templates without an override should use the global daemon refresh rate."""
    daemon = Daemon(Config(refresh_rate=3.0))
    template = _build_template("static_panel", None, None)
    registry = _DummyRegistry(template)

    profile = daemon._template_refresh_profile(registry, "static_panel", {})

    assert profile.requested_hz == 3.0
    assert profile.effective_hz == 3.0
    assert profile.getter_cap_hz is None
    assert profile.limiting_getters == ()


def test_apply_refresh_profile_logs_warning_once(caplog: object) -> None:
    """Cap warnings should log once per unchanged panel refresh profile."""
    daemon = Daemon(Config())
    panel = SimpleNamespace(
        name="primary",
        refresh_signature=None,
        current_render_interval=0.0,
        next_render_at=0.0,
    )
    profile = daemon._template_refresh_profile(
        _DummyRegistry(_build_template("system_stats", "cpu.percent", 5.0)),
        "system_stats",
        {"CpuGetter": _DummyGetter(DataStore(), interval=2.0)},
    )

    with caplog.at_level(logging.INFO, logger="casedd.daemon"):
        daemon._apply_refresh_profile(panel, "system_stats", profile, now=100.0)
        daemon._apply_refresh_profile(panel, "system_stats", profile, now=101.0)

    warning_messages = [
        record.message
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    info_messages = [
        record.message
        for record in caplog.records
        if record.levelno == logging.INFO
    ]
    assert len(warning_messages) == 1
    assert len(info_messages) == 1
    assert "capped to 0.500 Hz" in warning_messages[0]
