"""Tests for :mod:`casedd.renderer.widgets.table`."""

from __future__ import annotations

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.table import TableWidget, _split_phasing_suffix
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig, WidgetType


def test_table_widget_renders_pipe_rows() -> None:
    """Table widget should render non-empty pixels for pipe-delimited rows."""
    img = Image.new("RGB", (420, 220), (0, 0, 0))
    store = DataStore()
    store.set(
        "pihole.top_blocked.list",
        "dcape-na.amazon.com|9710\napi.segment.io|2283\nsdk.iad-02.braze.com|2195",
    )

    widget = TableWidget()
    cfg = WidgetConfig(
        type=WidgetType.TABLE,
        source="pihole.top_blocked.list",
        label="Top Blocked Domains",
        font_size="auto",
    )
    widget.draw(img, Rect(x=0, y=0, w=420, h=220), cfg, store, {})

    assert img.getbbox() is not None


def test_table_widget_strips_rank_prefixes() -> None:
    """Legacy rank prefixes should be stripped from the left column."""
    img = Image.new("RGB", (320, 160), (0, 0, 0))
    store = DataStore()
    store.set("legacy.table", "1. one.example.com|100\n2. two.example.com|90")

    widget = TableWidget()
    cfg = WidgetConfig(type=WidgetType.TABLE, source="legacy.table", font_size="auto")
    widget.draw(img, Rect(x=0, y=0, w=320, h=160), cfg, store, {})

    assert img.getbbox() is not None


def test_table_widget_fit_text_and_cache_reuse() -> None:
    """Fit-text mode should render repeatedly without overflow regressions."""
    img = Image.new("RGB", (520, 220), (0, 0, 0))
    store = DataStore()
    store.set(
        "pihole.top_blocked.list",
        "arcus-uswest.amazon.com|1142\n"
        "sdk.iad-02.braze.com|2195\n"
        "dcape-na.amazon.com|9710",
    )

    widget = TableWidget()
    cfg = WidgetConfig(
        type=WidgetType.TABLE,
        source="pihole.top_blocked.list",
        label="Top Blocked Domains",
        font_size="auto",
        table_fit_text=True,
        max_items=5,
    )
    state: dict[str, object] = {}

    widget.draw(img, Rect(x=0, y=0, w=520, h=220), cfg, store, state)
    widget.draw(img, Rect(x=0, y=0, w=520, h=220), cfg, store, state)

    assert img.getbbox() is not None
    assert "table_layout" in state


def test_table_widget_content_is_top_aligned() -> None:
    """Rows should anchor near the top instead of centering vertically."""
    img = Image.new("RGB", (320, 220), (0, 0, 0))
    store = DataStore()
    store.set("table.top", "first|1")

    widget = TableWidget()
    cfg = WidgetConfig(type=WidgetType.TABLE, source="table.top", font_size="auto")
    widget.draw(img, Rect(x=0, y=0, w=320, h=220), cfg, store, {})

    bbox = img.getbbox()
    assert bbox is not None
    # Top-aligned text should render near the top edge of the content rect.
    assert bbox[1] < 40


def test_table_widget_auto_font_is_bounded_for_single_row() -> None:
    """Single-row tables should not scale to oversized headline text."""
    img = Image.new("RGB", (540, 220), (0, 0, 0))
    store = DataStore()
    store.set("table.single", "paused|1.5GB")

    widget = TableWidget()
    cfg = WidgetConfig(type=WidgetType.TABLE, source="table.single", font_size="auto")
    widget.draw(img, Rect(x=0, y=0, w=540, h=220), cfg, store, {})

    bbox = img.getbbox()
    assert bbox is not None
    # Keep glyph height reasonable for readability in mixed table dashboards.
    assert (bbox[3] - bbox[1]) < 90


def test_split_phasing_suffix() -> None:
    """Table helper should isolate phasing suffix for muted rendering."""
    assert _split_phasing_suffix("1.2.3 [SEC] (phasing)") == (
        "1.2.3 [SEC]",
        " (phasing)",
    )
    assert _split_phasing_suffix("1.2.3 [SEC]") == ("1.2.3 [SEC]", "")


def test_table_widget_max_font_size_caps_auto_selection() -> None:
    """max_font_size should hard-cap auto font selection for single-row tables."""
    img_uncapped = Image.new("RGB", (540, 220), (0, 0, 0))
    img_capped = Image.new("RGB", (540, 220), (0, 0, 0))
    store = DataStore()
    store.set("queue.single", "paused|1.5GB")

    widget = TableWidget()
    state_uncapped: dict[str, object] = {}
    state_capped: dict[str, object] = {}

    # Draw without max_font_size.
    cfg_uncapped = WidgetConfig(
        type=WidgetType.TABLE, source="queue.single", font_size="auto"
    )
    widget.draw(img_uncapped, Rect(x=0, y=0, w=540, h=220), cfg_uncapped, store, state_uncapped)

    # Draw with max_font_size=8 to constrain dramatically.
    cfg_capped = WidgetConfig(
        type=WidgetType.TABLE,
        source="queue.single",
        font_size="auto",
        max_font_size=8,
    )
    widget.draw(img_capped, Rect(x=0, y=0, w=540, h=220), cfg_capped, store, state_capped)

    bbox_uncapped = img_uncapped.getbbox()
    bbox_capped = img_capped.getbbox()
    assert bbox_uncapped is not None
    assert bbox_capped is not None

    uncapped_h = bbox_uncapped[3] - bbox_uncapped[1]
    capped_h = bbox_capped[3] - bbox_capped[1]

    # Capped version should render noticeably smaller.
    assert capped_h < uncapped_h


def test_table_widget_renders_container_multicolumn_mode() -> None:
    """Containers table mode should render 5-column rows with icon columns."""
    img = Image.new("RGB", (900, 320), (0, 0, 0))
    store = DataStore()
    store.set(
        "containers.rows",
        "invokeai|exited|unknown|-|ghcr.io/invoke-ai/invokeai:latest\n"
        "ollama|started|healthy|3 hours|ollama/ollama",
    )

    widget = TableWidget()
    cfg = WidgetConfig(type=WidgetType.TABLE, source="containers.rows", font_size="auto")
    widget.draw(img, Rect(x=0, y=0, w=900, h=320), cfg, store, {})

    assert img.getbbox() is not None
