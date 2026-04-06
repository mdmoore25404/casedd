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

import asyncio
import logging
import mmap
from pathlib import Path
import re
import struct
from typing import TYPE_CHECKING

from PIL import Image

from casedd.outputs.base import OutputBackend

if TYPE_CHECKING:
    from casedd.config import OutputBackendConfig

_log = logging.getLogger(__name__)

# Framebuffer mode string pattern: TYPE:WxH[p|i]-HZ, e.g. "U:800x480p-60".
_MODE_RE = re.compile(r"^[A-Z]:(\d+)x(\d+)[pi]-(\d+(?:\.\d+)?)$")


def _read_sysfs_str(path: Path) -> str | None:
    """Read text from a sysfs pseudo-file.

    Args:
        path: Sysfs file path.

    Returns:
        Stripped text, or ``None`` on any read error.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_sysfs_int(path: Path, default: int) -> int:
    """Read a single integer from a sysfs pseudo-file.

    Args:
        path: Sysfs file path.
        default: Value to return if the file cannot be read.

    Returns:
        The integer value, or ``default`` on any error.
    """
    raw = _read_sysfs_str(path)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_fb_modes(sysfs_fb: Path) -> tuple[list[str], list[float]]:
    """Parse supported video modes from the sysfs ``modes`` file.

    Args:
        sysfs_fb: Sysfs directory for the framebuffer device.

    Returns:
        Tuple of (list of raw mode strings, sorted list of Hz values).
    """
    raw = _read_sysfs_str(sysfs_fb / "modes")
    if not raw:
        return [], []
    modes = [line.strip() for line in raw.splitlines() if line.strip()]
    hz_set: set[float] = set()
    for mode in modes:
        m = _MODE_RE.match(mode)
        if m:
            hz_set.add(float(m.group(3)))
    return modes, sorted(hz_set)


class FramebufferOutput(OutputBackend):
    """Writes PIL images to a Linux framebuffer device via mmap.

    Implements :class:`~casedd.outputs.base.OutputBackend` so it can be
    used with the pluggable backend system while remaining backward-
    compatible with the existing panel-based rendering path.

    Detects pixel format (RGB565 / 32-bit) automatically from sysfs.

    Args:
        device: Path to the framebuffer device (e.g. ``/dev/fb1``).
        disabled: When ``True``, all write calls are silent no-ops.
    """

    def __init__(self, device: Path, *, disabled: bool = False, rotation: int = 0) -> None:
        """Initialise the framebuffer output.

        Probes device availability and pixel format. If the device is not
        present, the output disables itself gracefully.

        Args:
            device: Framebuffer device path.
            disabled: Force-disable (e.g. ``CASEDD_NO_FB=1``).
        """
        self._device = device
        self._enabled = False
        # Initialised to empty; populated when the device is successfully opened.
        self._supported_modes: list[str] = []
        self._supported_hz: list[float] = []
        self._rotation = int(rotation) if rotation is not None else 0

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

        self._supported_modes, self._supported_hz = _parse_fb_modes(sysfs_base)

        _log.info(
            "Framebuffer: %s %dx%d %dbpp (%d bytes/frame)",
            device, fb_w, fb_h, self._bpp, self._frame_size,
        )
        if self._supported_modes:
            _log.info(
                "Framebuffer modes: %s",
                ", ".join(self._supported_modes),
            )
        if self._supported_hz:
            hz_str = ", ".join(f"{h:.0f} Hz" for h in self._supported_hz)
            _log.info("Framebuffer supported refresh rates: %s", hz_str)
        self._enabled = True

    @property
    def supported_modes(self) -> list[str]:
        """Supported video mode strings as reported by sysfs.

        Returns:
            List of mode strings (e.g. ``["U:800x480p-60"]``), or an
            empty list when the framebuffer is disabled or modes are not
            advertised by the driver.
        """
        return list(self._supported_modes)

    @property
    def supported_hz(self) -> list[float]:
        """Supported refresh rates in Hz, sorted ascending.

        Returns:
            List of distinct Hz values parsed from ``supported_modes``,
            or an empty list when unavailable.
        """
        return list(self._supported_hz)

    # ------------------------------------------------------------------
    # OutputBackend interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """No-op: framebuffer device is claimed lazily on first write.

        The device is opened and mmap'd on each :meth:`write` call, so no
        explicit startup action is needed.
        """

    async def stop(self) -> None:
        """Disable the output and release internal references.

        After calling ``stop``, further :meth:`output` calls are no-ops.
        """
        self._enabled = False
        _log.debug("FramebufferOutput.stop() called for %s", self._device)

    async def output(
        self,
        image: Image.Image,
        config: OutputBackendConfig | None = None,  # noqa: ARG002  # interface compat
    ) -> None:
        """Write one rendered frame to the framebuffer (non-blocking).

        Delegates to :meth:`write` via ``asyncio.to_thread`` so the event
        loop is never blocked by the mmap write.  The ``config`` parameter
        is accepted for interface compatibility but is not used here — the
        framebuffer device's native geometry governs format and resolution.

        Args:
            image: Rendered ``PIL.Image.Image`` in ``RGB`` mode, pre-scaled
                to this backend's declared resolution by the dispatch layer.
            config: Unused; present for :class:`OutputBackend` compatibility.
        """
        await asyncio.to_thread(self.write, image)

    def is_healthy(self) -> bool:
        """Return ``True`` if the framebuffer device is open and writable.

        Returns:
            ``True`` when enabled, ``False`` when disabled (device absent or
            a prior write error caused self-disabling).
        """
        return self._enabled

    def get_config(self) -> dict[str, object]:
        """Return a snapshot of the framebuffer backend's configuration.

        Returns:
            Mapping with ``device``, ``enabled``, ``rotation``, ``width``,
            ``height``, and ``bpp`` entries.
        """
        config: dict[str, object] = {
            "device": str(self._device),
            "enabled": self._enabled,
            "rotation": self._rotation,
        }
        if self._enabled:
            config["width"] = self._fb_w
            config["height"] = self._fb_h
            config["bpp"] = self._bpp
        return config

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
            # Apply rotation if required before writing. Use transpose for
            # 90/180/270-degree rotations to preserve pixel integrity.
            img = image
            if self._rotation in (90, 180, 270):
                if self._rotation == 90:
                    img = img.transpose(Image.Transpose.ROTATE_90)
                elif self._rotation == 180:
                    img = img.transpose(Image.Transpose.ROTATE_180)
                elif self._rotation == 270:
                    img = img.transpose(Image.Transpose.ROTATE_270)
            self._write_unsafe(img)
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

        if img.size != (self._fb_w, self._fb_h):
            img_w, img_h = img.size
            if img_w <= self._fb_w and img_h <= self._fb_h:
                # Render canvas is smaller than the physical framebuffer (e.g.
                # GPU outputs 4K over HDMI to a 1024x600 panel).  Place the
                # image at the top-left corner of a black full-fb canvas so the
                # display shows pixel-perfect content without upscaling.
                canvas = Image.new("RGB", (self._fb_w, self._fb_h), (0, 0, 0))
                canvas.paste(img, (0, 0))
                img = canvas
            else:
                # Render canvas exceeds the framebuffer — scale down to fit.
                img = img.resize(
                    (self._fb_w, self._fb_h),
                    Image.Resampling.BILINEAR,
                )

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
        pixels: list[tuple[int, int, int]] = list(img.getdata())
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
        # Use Pillow's C-level raw exporter instead of a Python pixel loop.
        # This is significantly faster for large framebuffers (e.g. 4K).
        return img.tobytes("raw", "BGRX")
