"""Tests for :mod:`casedd.renderer.widgets.ollama`."""

from __future__ import annotations

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.ollama import OllamaWidget
from casedd.template.grid import Rect
from casedd.template.models import WidgetConfig, WidgetType


def test_ollama_widget_renders_running_rows() -> None:
    """Widget should render non-empty output for populated running-model keys."""
    img = Image.new("RGB", (520, 260), (0, 0, 0))
    store = DataStore()
    store.set("ollama.version", "0.6.0")
    store.set("ollama.models.local_count", 12.0)
    store.set("ollama.models.running_count", 2.0)
    store.set("ollama.running_1.name", "llama3.2:latest")
    store.set("ollama.running_1.size_vram_bytes", 5_100_000_000.0)
    store.set("ollama.running_1.ttl", "43m")
    store.set("ollama.running_2.name", "qwen3:latest")
    store.set("ollama.running_2.size_vram_bytes", 3_800_000_000.0)
    store.set("ollama.running_2.ttl", "1h 12m")

    widget = OllamaWidget()
    cfg = WidgetConfig(type=WidgetType.OLLAMA, source="ollama", max_items=6, font_size="auto")
    widget.draw(img, Rect(x=0, y=0, w=520, h=260), cfg, store, {})

    assert img.getbbox() is not None


def test_ollama_widget_handles_empty_state() -> None:
    """Widget should render gracefully when there are no running models."""
    img = Image.new("RGB", (420, 180), (0, 0, 0))
    store = DataStore()
    store.set("ollama.version", "0.6.0")
    store.set("ollama.models.local_count", 0.0)
    store.set("ollama.models.running_count", 0.0)

    widget = OllamaWidget()
    cfg = WidgetConfig(type=WidgetType.OLLAMA, source="ollama")
    widget.draw(img, Rect(x=0, y=0, w=420, h=180), cfg, store, {})

    assert img.getbbox() is not None
