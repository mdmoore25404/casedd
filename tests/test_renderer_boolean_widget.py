"""Tests for :mod:`casedd.renderer.widgets.boolean`."""

from __future__ import annotations

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.boolean import BooleanWidget
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig, WidgetType


def test_boolean_widget_true_draws_green_icon() -> None:
    """A truthy value should render a green-dominant status icon."""
    img = Image.new("RGB", (120, 120), (0, 0, 0))
    store = DataStore()
    store.set("pihole.blocking.enabled", 1)

    widget = BooleanWidget()
    cfg = WidgetConfig(type=WidgetType.BOOLEAN, source="pihole.blocking.enabled")
    widget.draw(img, Rect(x=0, y=0, w=120, h=120), cfg, store, {})

    check = img.getpixel((52, 82))
    assert check[1] > check[0]
    assert check[1] > check[2]


def test_boolean_widget_false_draws_red_icon() -> None:
    """A falsey value should render a red-dominant status icon."""
    img = Image.new("RGB", (120, 120), (0, 0, 0))
    store = DataStore()
    store.set("pihole.blocking.enabled", 0)

    widget = BooleanWidget()
    cfg = WidgetConfig(type=WidgetType.BOOLEAN, source="pihole.blocking.enabled")
    widget.draw(img, Rect(x=0, y=0, w=120, h=120), cfg, store, {})

    slash = img.getpixel((60, 60))
    assert slash[0] > slash[1]
    assert slash[0] > slash[2]
