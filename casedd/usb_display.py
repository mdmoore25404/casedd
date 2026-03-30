"""USB framebuffer display detection and capability enumeration.

Scans the Linux sysfs filesystem to discover all framebuffer devices,
determine which are USB-connected, and read their hardware capabilities
(resolution, pixel depth, supported video modes and refresh rates).

A USB framebuffer is identified by resolving the ``device`` symlink under
``/sys/class/graphics/fbX/`` and checking whether the resulting path
traverses a USB bus (i.e. contains ``/usb`` as a path component).

Public API:
    - :class:`FramebufferInfo` — properties of one framebuffer device
    - :func:`probe_framebuffer` — probe a single ``/dev/fbX`` device
    - :func:`find_framebuffers` — enumerate all fb devices with properties
    - :func:`find_usb_framebuffers` — filter to USB-connected devices only
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import re

_log = logging.getLogger(__name__)

# Sysfs root for all framebuffer class devices.
_FB_CLASS = Path("/sys/class/graphics")

# Framebuffer mode string format: ``TYPE:WxH[p|i]-HZ``
# Examples: ``U:800x480p-60``, ``V:1920x1080p-60``
_MODE_RE = re.compile(r"^[A-Z]:(\d+)x(\d+)[pi]-(\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class FramebufferInfo:
    """Properties of one Linux framebuffer device.

    Attributes:
        device: Path to the device node, e.g. ``/dev/fb1``.
        width: Horizontal resolution in pixels (0 if unreadable).
        height: Vertical resolution in pixels (0 if unreadable).
        bpp: Bits per pixel (typically 16 or 32).
        is_usb: ``True`` when the underlying hardware is USB-connected.
        usb_vendor_id: 4-char hex USB vendor ID, or ``None`` if not USB.
        usb_product_id: 4-char hex USB product ID, or ``None`` if not USB.
        driver: Kernel driver name (e.g. ``udlfb``, ``fbtft``), or ``None``.
        supported_modes: Mode strings as reported by sysfs
            (e.g. ``["U:800x480p-60"]``).
        supported_hz: Distinct refresh rates (Hz) parsed from
            ``supported_modes``, sorted ascending.
    """

    device: Path
    width: int
    height: int
    bpp: int
    is_usb: bool
    usb_vendor_id: str | None
    usb_product_id: str | None
    driver: str | None
    supported_modes: list[str] = field(default_factory=list)
    supported_hz: list[float] = field(default_factory=list)

    @property
    def resolution(self) -> tuple[int, int]:
        """Return ``(width, height)`` as a convenience tuple.

        Returns:
            Tuple of (width, height) in pixels.
        """
        return (self.width, self.height)

    def describe(self) -> str:
        """Return a concise human-readable description of this display.

        Returns:
            Single-line summary including device path, resolution, bpp,
            refresh rate(s), USB IDs, and driver name.
        """
        usb_tag = ""
        if self.is_usb and self.usb_vendor_id and self.usb_product_id:
            usb_tag = f" USB {self.usb_vendor_id}:{self.usb_product_id}"
        elif self.is_usb:
            usb_tag = " USB"

        hz_tag = ""
        if self.supported_hz:
            hz_list = "/".join(f"{h:.0f}" for h in self.supported_hz)
            hz_tag = f" @{hz_list}Hz"

        driver_tag = f" [{self.driver}]" if self.driver else ""
        return (
            f"{self.device} {self.width}x{self.height} {self.bpp}bpp"
            f"{hz_tag}{usb_tag}{driver_tag}"
        )


# ---------------------------------------------------------------------------
# Internal sysfs helpers
# ---------------------------------------------------------------------------


def _read_sysfs_str(path: Path) -> str | None:
    """Read text from a sysfs pseudo-file.

    Args:
        path: Sysfs file path to read.

    Returns:
        Stripped text content, or ``None`` on any read error.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_sysfs_int(path: Path, default: int) -> int:
    """Read a single integer from a sysfs pseudo-file.

    Args:
        path: Sysfs file path.
        default: Value to return if the file cannot be read or parsed.

    Returns:
        Parsed integer, or ``default`` on any error.
    """
    raw = _read_sysfs_str(path)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_modes(sysfs_fb: Path) -> tuple[list[str], list[float]]:
    """Parse supported video modes from the sysfs ``modes`` file.

    The ``modes`` file contains one mode string per line in the format
    ``TYPE:WxH[p|i]-HZ``, e.g. ``U:800x480p-60``.

    Args:
        sysfs_fb: Sysfs directory for the framebuffer device
            (e.g. ``/sys/class/graphics/fb1``).

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


def _find_usb_device_dir(sysfs_device_resolved: Path) -> Path | None:
    """Walk up the sysfs tree to locate the USB device directory.

    The USB device directory is the ancestor that contains both
    ``idVendor`` and ``idProduct`` files.

    Args:
        sysfs_device_resolved: Already-resolved sysfs device path.

    Returns:
        Path to the USB device directory, or ``None`` if not found within
        a reasonable number of steps.
    """
    current = sysfs_device_resolved
    for _ in range(10):  # guard against runaway traversal
        if (current / "idVendor").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _probe_usb_info(sysfs_fb: Path) -> tuple[bool, str | None, str | None]:
    """Determine whether a framebuffer device is USB-connected.

    Resolves the ``device`` symlink under the sysfs framebuffer directory
    and checks whether the path traverses a USB bus node.

    Args:
        sysfs_fb: Sysfs directory for the framebuffer device.

    Returns:
        Tuple of ``(is_usb, vendor_id_or_none, product_id_or_none)``.
        ``vendor_id`` and ``product_id`` are 4-char lowercase hex strings
        when available.
    """
    device_link = sysfs_fb / "device"
    try:
        resolved = device_link.resolve(strict=False)
    except OSError:
        return False, None, None

    # A USB-connected device's sysfs path contains a ``/usbN/`` component.
    if "/usb" not in str(resolved):
        return False, None, None

    usb_dir = _find_usb_device_dir(resolved)
    if usb_dir is None:
        # USB path confirmed but couldn't read IDs — still flag as USB.
        return True, None, None

    vendor = _read_sysfs_str(usb_dir / "idVendor")
    product = _read_sysfs_str(usb_dir / "idProduct")
    return True, vendor, product


def _get_driver(sysfs_fb: Path) -> str | None:
    """Read the kernel driver name for a framebuffer device.

    Resolves the ``device/driver`` symlink; its basename is the driver name.

    Args:
        sysfs_fb: Sysfs directory for the framebuffer device.

    Returns:
        Driver name string (e.g. ``"udlfb"``), or ``None`` if unavailable.
    """
    driver_link = sysfs_fb / "device" / "driver"
    try:
        resolved = driver_link.resolve(strict=False)
        return resolved.name if resolved.name else None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def probe_framebuffer(device: Path) -> FramebufferInfo | None:
    """Probe a single framebuffer device and return its properties.

    Reads resolution, bit depth, supported modes, and USB identity from
    the kernel sysfs tree at ``/sys/class/graphics/<name>/``.

    Args:
        device: Path to the device node, e.g. ``/dev/fb1``.

    Returns:
        A :class:`FramebufferInfo` describing the device, or ``None`` if
        the device node does not exist.
    """
    if not device.exists():
        return None

    dev_name = device.name  # e.g. "fb1"
    sysfs_fb = _FB_CLASS / dev_name

    virtual_size_raw = _read_sysfs_str(sysfs_fb / "virtual_size")
    if virtual_size_raw:
        try:
            fb_w, fb_h = (int(x) for x in virtual_size_raw.split(","))
        except ValueError:
            fb_w, fb_h = 0, 0
    else:
        fb_w, fb_h = 0, 0

    bpp = _read_sysfs_int(sysfs_fb / "bits_per_pixel", 16)
    supported_modes, supported_hz = _parse_modes(sysfs_fb)
    is_usb, vendor_id, product_id = _probe_usb_info(sysfs_fb)
    driver = _get_driver(sysfs_fb)

    return FramebufferInfo(
        device=device,
        width=fb_w,
        height=fb_h,
        bpp=bpp,
        is_usb=is_usb,
        usb_vendor_id=vendor_id,
        usb_product_id=product_id,
        driver=driver,
        supported_modes=supported_modes,
        supported_hz=supported_hz,
    )


def find_framebuffers() -> list[FramebufferInfo]:
    """Enumerate all Linux framebuffer devices and their properties.

    Scans ``/dev/fb*`` and probes each device via sysfs.  Devices are
    sorted by device name (i.e. ``fb0`` before ``fb1``).

    Returns:
        List of :class:`FramebufferInfo` for every framebuffer found.
        Returns an empty list when no framebuffer device nodes exist.
    """
    devices = sorted(Path("/dev").glob("fb*"))
    result: list[FramebufferInfo] = []
    for dev in devices:
        info = probe_framebuffer(dev)
        if info is not None:
            result.append(info)
            _log.debug("Framebuffer detected: %s", info.describe())
    return result


def find_usb_framebuffers() -> list[FramebufferInfo]:
    """Return only USB-connected framebuffer devices.

    Calls :func:`find_framebuffers` and filters to entries where
    ``is_usb`` is ``True``.

    Returns:
        Filtered list of :class:`FramebufferInfo`, sorted by device name.
        Returns an empty list when no USB framebuffers are detected.
    """
    all_fbs = find_framebuffers()
    usb_fbs = [fb for fb in all_fbs if fb.is_usb]
    if usb_fbs:
        _log.info(
            "USB framebuffer(s) found: %s",
            ", ".join(fb.describe() for fb in usb_fbs),
        )
    else:
        _log.debug("No USB-connected framebuffer devices detected.")
    return usb_fbs
