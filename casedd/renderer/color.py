"""Color parsing and interpolation utilities for CASEDD.

Centralises all color handling so widgets don't each implement their own
hex/rgb parsing. Supports hex triplets, 3-digit hex shortcuts, CSS rgb(),
and HTML named colors. Also handles ``color_stops`` gradient interpolation.

Public API:
    - :func:`parse_color` — string → ``(r, g, b)`` tuple
    - :func:`interpolate_color_stops` — value → interpolated ``(r, g, b)``
"""

from __future__ import annotations

import re

# Pre-compiled hex pattern for fast repeated calls in the render loop
_HEX6 = re.compile(r"^#([0-9a-fA-F]{6})$")
_HEX3 = re.compile(r"^#([0-9a-fA-F]{3})$")
_RGB = re.compile(r"^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", re.IGNORECASE)

# Subset of HTML named colors used in CSS; extend as needed
_NAMED: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "orange": (255, 165, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "darkgray": (64, 64, 64),
    "darkgrey": (64, 64, 64),
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "transparent": (0, 0, 0),  # PIL handles alpha separately; treat as black
}

RGBTuple = tuple[int, int, int]


def parse_color(color: str | None, fallback: RGBTuple = (255, 255, 255)) -> RGBTuple:
    """Parse a color string into an ``(r, g, b)`` tuple.

    Args:
        color: A color string in hex (``"#rrggbb"`` or ``"#rgb"``),
               CSS rgb (``"rgb(r,g,b)"``), or HTML named color format.
               ``None`` returns ``fallback``.
        fallback: Return value when ``color`` is ``None`` or unrecognised.

    Returns:
        ``(r, g, b)`` tuple with each component in 0-255.
    """
    if color is None:
        return fallback

    color = color.strip()

    m = _HEX6.match(color)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    m = _HEX3.match(color)
    if m:
        h = m.group(1)
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))

    m = _RGB.match(color)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    named = _NAMED.get(color.lower())
    if named is not None:
        return named

    return fallback


def interpolate_color_stops(
    value: float,
    stops: list[list[float | str]],
    _v_min: float = 0.0,
    _v_max: float = 100.0,
) -> RGBTuple:
    """Interpolate a color from a ``color_stops`` list for a given value.

    Color stops are ``[threshold, color]`` pairs sorted by threshold. The
    returned color is linearly interpolated between the two nearest stops.

    Args:
        value: The current value to colorize.
        stops: List of ``[threshold, color_string]`` pairs (unsorted OK).
        v_min: Minimum possible value (used when stops list is empty).
        v_max: Maximum possible value (used when stops list is empty).

    Returns:
        Interpolated ``(r, g, b)`` tuple.
    """
    if not stops:
        # No stops — return a neutral gray
        return (180, 180, 180)

    # Sort by threshold ascending
    parsed: list[tuple[float, RGBTuple]] = sorted(
        ((float(s[0]), parse_color(str(s[1]))) for s in stops),
        key=lambda t: t[0],
    )

    # Clamp to the defined range
    if value <= parsed[0][0]:
        return parsed[0][1]
    if value >= parsed[-1][0]:
        return parsed[-1][1]

    # Find the two surrounding stops and interpolate
    for i in range(len(parsed) - 1):
        lo_thresh, lo_color = parsed[i]
        hi_thresh, hi_color = parsed[i + 1]
        if lo_thresh <= value <= hi_thresh:
            span = hi_thresh - lo_thresh
            if span == 0:
                return hi_color
            t = (value - lo_thresh) / span  # 0.0 → 1.0
            return (
                int(lo_color[0] + t * (hi_color[0] - lo_color[0])),
                int(lo_color[1] + t * (hi_color[1] - lo_color[1])),
                int(lo_color[2] + t * (hi_color[2] - lo_color[2])),
            )

    return parsed[-1][1]  # unreachable but satisfies type checker
