"""Tests for multi-series legend color metadata in chart widgets."""

from __future__ import annotations

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.histogram import HistogramWidget
from casedd.renderer.widgets.sparkline import SparklineWidget
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig, WidgetType


def test_histogram_legend_entries_use_series_colors() -> None:
    """Histogram legend entries should resolve colors from the configured series."""
    widget = HistogramWidget()
    cfg = WidgetConfig(
        type=WidgetType.HISTOGRAM,
        sources=["net.recv_mbps", "net.sent_mbps"],
        series_labels=["Dn", "Up"],
        series_colors=["#22cc88", "#ffaa22"],
        precision=1,
        unit="Mb/s",
    )

    entries = widget._multi_legend_entries(
        cfg,
        {"net.recv_mbps": 12.3, "net.sent_mbps": 4.5},
    )

    assert [entry.text for entry in entries] == ["Dn 12.3Mb/s", "Up 4.5Mb/s"]
    assert [entry.color for entry in entries] == [(34, 204, 136), (255, 170, 34)]



def test_sparkline_legend_entries_use_series_colors() -> None:
    """Sparkline legend entries should resolve colors from the configured series."""
    widget = SparklineWidget()
    cfg = WidgetConfig(
        type=WidgetType.SPARKLINE,
        sources=["net.recv_mbps", "net.sent_mbps"],
        series_labels=["Dn", "Up"],
        series_colors=["#22cc88", "#ffaa22"],
        precision=2,
        unit="Mb/s",
    )

    entries = widget._legend_entries(
        cfg,
        {"net.recv_mbps": 12.34, "net.sent_mbps": 4.56},
    )

    assert [entry.text for entry in entries] == ["Dn 12.34 Mb/s", "Up 4.56 Mb/s"]
    assert [entry.color for entry in entries] == [(34, 204, 136), (255, 170, 34)]


def test_single_series_sparkline_draws_first_sample_value() -> None:
    """Single-series sparkline should render current-value text on first sample."""
    widget = SparklineWidget()
    cfg = WidgetConfig(
        type=WidgetType.SPARKLINE,
        source="cpu.percent",
        precision=1,
        unit="%",
        color="#ffffff",
    )
    store = DataStore()
    store.set("cpu.percent", 42.0)
    image = Image.new("RGB", (220, 120), (0, 0, 0))
    state: dict[str, object] = {}

    widget.draw(image, Rect(x=0, y=0, w=220, h=120), cfg, store, state)

    assert image.getbbox() is not None
