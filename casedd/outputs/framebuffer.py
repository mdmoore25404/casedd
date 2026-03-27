"""Framebuffer output: direct mmap write to /dev/fb1 (or configured path).

Writes a PIL Image directly to the Linux framebuffer device using ``mmap``.
Supports RGB565 (16-bit) and XRGB8888/ARGB8888 (32-bit) pixel formats,
auto-detected from the kernel sysfs ``bits_per_pixel`` file.

Requires the running user to be in the ``video`` group — no root needed.

Falls back to a clean no-op when:
- ``CASEDD_NO_FB=1`` is set, or
- The framebuffer device does not exist.

Public API:
    - :class:`FramebufferOutput` — write PIL images to the framebuffer
"""

from __future__ import annotations

import logging
import mmap
from pathlib import Path
import struct

from PIL import Image

_log = logging.getLogger(__name__)


def _read_sysfs_int(path: Path, default: int) -> int:
    """Read a single integer from a sysfs pseudo-file.

    Args:
        path: Sysfs file path.
        default: Value to return if the file cannot be read.

    Returns:
        The integer value, or ``default`` on any error.
    """
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return default


class FramebufferOutput:
    """Writes PIL images to a Linux framebuffer device via mmap.

    Detects pixel format (RGB565 / 32-bit) automatically from sysfs.

    Args:
        device: Path to the framebuffer device (e.g. ``/dev/fb1``).
        disabled: When ``True``, all write calls are silent no-ops.
    """

    def __init__(self, device: Path, *, disabled: bool = False) -> None:
        """Initialise the framebuffer output.

        Probes device availability and pixel format. If the device is not
        present, the output disables itself gracefully.

        Args:
            device: Framebuffer device path.
            disabled: Force-disable (e.g. ``CASEDD_NO_FB=1``).
        """
        self._device = device
        self._enabled = False

        if disabled:
            _log.info("Framebuffer output disabled (CASEDD_NO_FB=1).")
            return

        if not device.exists():
            _log.warning(
                "Framebuffer device '%s' not found — framebuffer output disabled.", device
            )
            return

        # Read framebuffer geometry from sysfs
        dev_name = device.name  # e.g. "fb1"
        sysfs_base = Path(f"/sys/class/graphics/{dev_name}")
        virtual_size_str = sysfs_base / "virtual_size"

        try:
            vs = virtual_size_str.read_text(encoding="utf-8").strip()
            fb_w, fb_h = (int(x) for x in vs.split(","))
        except (OSError, ValueError):
            fb_w, fb_h = 800, 480
            _log.warning(
                "Could not read virtual_size for %s -- assuming %dx%d.",
                device, fb_w, fb_h,
            )

        self._fb_w = fb_w
        self._fb_h = fb_h
        self._bpp: int = _read_sysfs_int(sysfs_base / "bits_per_pixel", 16)
        self._bytes_per_pixel = self._bpp // 8
        self._frame_size = fb_w * fb_h * self._bytes_per_pixel

        _log.info(
            "Framebuffer: %s %dx%d %dbpp (%d bytes/frame)",
            device, fb_w, fb_h, self._bpp, self._frame_size,
        )
        self._enabled = True

    def write(self, image: Image.Image) -> None:
        """Write a PIL image to the framebuffer.

        Rescales the image if it doesn't match the framebuffer dimensions.
        A no-op when the output is disabled.

        Args:
            image: The PIL image to display (RGB mode expected).
        """
        if not self._enabled:
            return

        try:
            self._write_unsafe(image)
        except OSError as exc:
            _log.error("Framebuffer write error: %s — disabling output.", exc)
            self._enabled = False

    def _write_unsafe(self, image: Image.Image) -> None:
        """Internal write — caller must handle OSError.

        Args:
            image: PIL Image (will be converted and resized as needed).
        """
        # Ensure correct pixel format before conversion
        img = image.convert("RGB")

        # Resize to match framebuffer resolution if needed
        if img.size != (self._fb_w, self._fb_h):
            img = img.resize((self._fb_w, self._fb_h), Image.LANCZOS)  # type: ignore[attr-defined]

        raw_bytes = self._rgb_to_rgb565(img) if self._bpp == 16 else self._rgb_to_xrgb8888(img)

        with self._device.open("r+b") as fh, mmap.mmap(
            fh.fileno(), self._frame_size, mmap.MAP_SHARED, mmap.PROT_WRITE
        ) as mm:
            mm.seek(0)
            mm.write(raw_bytes)

    @staticmethod
    def _rgb_to_rgb565(img: Image.Image) -> bytes:
        """Convert a PIL RGB image to raw RGB565 bytes (little-endian).

        RGB565 packs 5 bits of red, 6 bits of green, and 5 bits of blue into
        a 16-bit word. This is the most common framebuffer format for small
        embedded displays.

        Args:
            img: PIL Image in RGB mode.

        Returns:
            Raw bytes suitable for writing to a 16-bpp framebuffer.
        """
        pixels = img.getdata()
        buf = bytearray(len(pixels) * 2)
        offset = 0
        for r, g, b in pixels:
            # Pack RGB565: RRRRRGGGGGGBBBBB
            pixel = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            struct.pack_into("<H", buf, offset, pixel)
            offset += 2
        return bytes(buf)

    @staticmethod
    def _rgb_to_xrgb8888(img: Image.Image) -> bytes:
        """Convert a PIL RGB image to raw XRGB8888 bytes.

        The X (alpha) byte is set to 0xFF for compatibility with displays
        that treat it as an alpha channel.

        Args:
            img: PIL Image in RGB mode.

        Returns:
            Raw bytes suitable for writing to a 32-bpp framebuffer.
        """
        pixels = img.getdata()
        buf = bytearray(len(pixels) * 4)
        offset = 0
        for r, g, b in pixels:
            struct.pack_into("<BBBB", buf, offset, b, g, r, 0xFF)
            offset += 4
        return bytes(buf)
