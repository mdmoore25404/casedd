"""Tests for :mod:`casedd.renderer.widgets.text`."""

from __future__ import annotations

from casedd.renderer.fonts import get_font
from casedd.renderer.widgets.text import _wrap_text


def test_wrap_text_splits_oversized_tokens() -> None:
    """Long single tokens should be split into multiple wrapped lines."""
    font = get_font(18)
    lines = _wrap_text("averyverylongsinglewordwithoutspaces", font, max_width=80)

    assert len(lines) > 1
    for line in lines:
        bbox = font.getbbox(line)
        width = bbox[2] - bbox[0]
        assert width <= 80
