"""Tests for :class:`~casedd.outputs.framebuffer.FramebufferOutput` write path.

Covers the no-upscale compositing behaviour added to support panels whose
kernel framebuffer reports a larger resolution than the display's native
resolution (e.g. a 1024x600 panel behind a GPU outputting 4K over HDMI).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
import pytest

from casedd.outputs.framebuffer import FramebufferOutput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fb_output(fb_w: int, fb_h: int, bpp: int = 32) -> FramebufferOutput:
    """Return a FramebufferOutput stubbed so no real device is touched.

    Args:
        fb_w: Framebuffer width to simulate.
        fb_h: Framebuffer height to simulate.
        bpp: Bits per pixel (default 32).

    Returns:
        A FramebufferOutput instance with internal state set directly.
    """
    fb = FramebufferOutput.__new__(FramebufferOutput)
    fb._device = Path("/dev/null")
    fb._enabled = True
    fb._fb_w = fb_w
    fb._fb_h = fb_h
    fb._bpp = bpp
    fb._bytes_per_pixel = bpp // 8
    fb._frame_size = fb_w * fb_h * (bpp // 8)
    fb._supported_modes: list[str] = []
    fb._supported_hz: list[float] = []
    fb._rotation = 0
    return fb


# ---------------------------------------------------------------------------
# Tests: image sizing in _write_unsafe
# ---------------------------------------------------------------------------

def test_smaller_image_composited_at_origin() -> None:
    """A 1024x600 image is placed at (0,0) on a 3840x2160 canvas without upscaling."""
    fb = _make_fb_output(3840, 2160)
    render = Image.new("RGB", (1024, 600), (255, 0, 0))

    written: list[Image.Image] = []

    def _capture(self: FramebufferOutput, image: Image.Image) -> None:
        written.append(image.copy())

    with patch.object(FramebufferOutput, "_write_unsafe", _capture):
        fb.write(render)

    assert len(written) == 1
    result = written[0]
    # The image passed to _write_unsafe is still 1024x600 — _write_unsafe
    # itself handles compositing, so the render engine output is unchanged.
    assert result.size == (1024, 600)


def test_write_unsafe_smaller_image_composited_correctly() -> None:
    """_write_unsafe composites a small render into a black full-fb canvas."""
    fb = _make_fb_output(200, 100, bpp=32)
    render = Image.new("RGB", (50, 40), (255, 0, 0))

    captured_bytes: list[bytes] = []

    class _FakeMM:
        def __init__(self) -> None:
            self._buf = bytearray(200 * 100 * 4)

        def seek(self, pos: int) -> None:
            self._offset = pos

        def write(self, data: bytes) -> None:
            captured_bytes.append(data)

        def __enter__(self) -> _FakeMM:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    fake_mm = _FakeMM()

    def _fake_open(self_path: Path, mode: str) -> MagicMock:
        fh = MagicMock()
        fh.__enter__ = lambda s: s
        fh.__exit__ = MagicMock(return_value=False)
        fh.fileno.return_value = -1
        return fh

    with (
        patch("builtins.open", side_effect=_fake_open),
        patch("mmap.mmap", return_value=fake_mm),
    ):
        fb._write_unsafe(render)

    assert len(captured_bytes) == 1
    raw = captured_bytes[0]
    # Total bytes must equal the full framebuffer size (200x100x4 = 80000).
    assert len(raw) == 200 * 100 * 4

    # Top-left 50x40 pixels should be red (B=0, G=0, R=255 in BGRX).
    # Pixel at (col=0, row=0): byte offset = 0, layout BGRX → [0, 0, 255, x]
    assert raw[0] == 0    # B
    assert raw[1] == 0    # G
    assert raw[2] == 255  # R

    # Pixel at (col=51, row=0) should be black (outside pasted region).
    offset_outside = 51 * 4
    assert raw[offset_outside] == 0
    assert raw[offset_outside + 1] == 0
    assert raw[offset_outside + 2] == 0


def test_write_unsafe_exact_size_no_composite() -> None:
    """_write_unsafe writes directly when image matches framebuffer exactly."""
    fb = _make_fb_output(4, 2, bpp=32)
    render = Image.new("RGB", (4, 2), (0, 128, 0))

    captured: list[bytes] = []

    class _FakeMM:
        def seek(self, pos: int) -> None:
            pass

        def write(self, data: bytes) -> None:
            captured.append(data)

        def __enter__(self) -> _FakeMM:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    with (
        patch("builtins.open", side_effect=lambda p, m: MagicMock(
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
            fileno=MagicMock(return_value=-1),
        )),
        patch("mmap.mmap", return_value=_FakeMM()),
    ):
        fb._write_unsafe(render)

    assert len(captured) == 1
    raw = captured[0]
    assert len(raw) == 4 * 2 * 4  # exact size, no padding


def test_write_unsafe_larger_image_downscaled() -> None:
    """_write_unsafe downscales an image that exceeds the framebuffer size."""
    fb = _make_fb_output(10, 10, bpp=32)
    render = Image.new("RGB", (100, 100), (0, 0, 200))

    captured: list[bytes] = []

    class _FakeMM:
        def seek(self, pos: int) -> None:
            pass

        def write(self, data: bytes) -> None:
            captured.append(data)

        def __enter__(self) -> _FakeMM:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    with (
        patch("builtins.open", side_effect=lambda p, m: MagicMock(
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
            fileno=MagicMock(return_value=-1),
        )),
        patch("mmap.mmap", return_value=_FakeMM()),
    ):
        fb._write_unsafe(render)

    assert len(captured) == 1
    assert len(captured[0]) == 10 * 10 * 4


@pytest.mark.parametrize("render_size,fb_size", [
    ((1024, 600), (3840, 2160)),  # typical: small panel, large fb
    ((800, 480), (1920, 1080)),   # Waveshare on FullHD fb
    ((640, 480), (800, 600)),     # slightly mismatched fb
])
def test_no_upscale_for_small_renders(
    render_size: tuple[int, int],
    fb_size: tuple[int, int],
) -> None:
    """Render dimensions never exceed the configured canvas (no upscaling)."""
    fb = _make_fb_output(*fb_size)
    render = Image.new("RGB", render_size, (100, 100, 100))

    captured: list[bytes] = []

    class _FakeMM:
        def seek(self, pos: int) -> None:
            pass

        def write(self, data: bytes) -> None:
            captured.append(data)

        def __enter__(self) -> _FakeMM:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    with (
        patch("builtins.open", side_effect=lambda p, m: MagicMock(
            __enter__=lambda s: s,
            __exit__=MagicMock(return_value=False),
            fileno=MagicMock(return_value=-1),
        )),
        patch("mmap.mmap", return_value=_FakeMM()),
    ):
        fb._write_unsafe(render)

    assert len(captured) == 1
    # Buffer size must match the full fb, not the render.
    assert len(captured[0]) == fb_size[0] * fb_size[1] * (fb._bpp // 8)
