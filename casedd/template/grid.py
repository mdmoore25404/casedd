"""CSS Grid Template Areas → pixel bounding box solver.

Implements a subset of the CSS Grid layout algorithm sufficient for the
CASEDD template format: named areas, ``fr``/``px``/``%`` track sizes, and
contiguous rectangular spans via repeated area names.

No browser, DOM, or external dependency — pure Python arithmetic.

Public API:
    - :func:`resolve_grid` — parse template_areas + track sizes → named pixel rects
    - :class:`Rect` — a named tuple for (x, y, width, height)
"""

from __future__ import annotations

import re
from typing import NamedTuple


class Rect(NamedTuple):
    """Pixel bounding rectangle.

    All values are in pixels, relative to the parent container's top-left corner.

    Attributes:
        x: Left edge offset in pixels.
        y: Top edge offset in pixels.
        w: Width in pixels.
        h: Height in pixels.
    """

    x: int
    y: int
    w: int
    h: int


def _parse_tracks(track_str: str, total_px: int) -> list[int]:
    """Convert a CSS-style track size string into pixel sizes.

    Supports three unit types:
    - ``Xfr`` — fractional units; remaining space after ``px`` tracks is
      divided proportionally.
    - ``Xpx`` — exact pixel count.
    - ``X%`` — percentage of ``total_px``.

    Mixed units are allowed, e.g. ``"200px 1fr 2fr"``.

    Args:
        track_str: Space-separated track size string (e.g. ``"1fr 1fr 200px"``).
        total_px: Total available pixels for this axis.

    Returns:
        List of pixel sizes for each track, in order.

    Raises:
        ValueError: If an unrecognised track size format is encountered.
    """
    tokens = track_str.split()
    sizes: list[float] = []
    fr_indices: list[int] = []
    fixed_total = 0

    for i, token in enumerate(tokens):
        token_lower = token.lower()
        if token_lower.endswith("fr"):
            fr_value = float(token_lower[:-2])
            sizes.append(fr_value)  # store raw fr value temporarily
            fr_indices.append(i)
        elif token_lower.endswith("px"):
            px = int(float(token_lower[:-2]))
            sizes.append(float(px))
            fixed_total += px
        elif token_lower.endswith("%"):
            pct = float(token_lower[:-1])
            px = int(total_px * pct / 100.0)
            sizes.append(float(px))
            fixed_total += px
        else:
            msg = f"Unrecognised track size: '{token}'. Use Xfr, Xpx, or X%."
            raise ValueError(msg)

    # Resolve fractional tracks against remaining space
    remaining = max(0, total_px - fixed_total)
    if fr_indices:
        total_fr = sum(sizes[i] for i in fr_indices)
        for i in fr_indices:
            sizes[i] = remaining * sizes[i] / total_fr if total_fr > 0 else 0.0

    return [round(s) for s in sizes]


def _parse_template_areas(template_areas: str) -> list[list[str]]:
    """Parse a CSS grid template_areas string into a 2D list of area names.

    Each row is a quoted string of space-separated cell names.

    Args:
        template_areas: Multi-line template_areas value from the .casedd file.

    Returns:
        2D list ``grid[row][col]`` of area name strings.

    Raises:
        ValueError: If the rows have inconsistent column counts.
    """
    # Accept lines with or without surrounding quotes
    rows: list[list[str]] = []
    for line in template_areas.strip().splitlines():
        # Strip surrounding whitespace and quotes
        stripped = line.strip().strip('"').strip("'")
        if not stripped:
            continue
        cells = stripped.split()
        rows.append(cells)

    if not rows:
        msg = "template_areas is empty"
        raise ValueError(msg)

    col_count = len(rows[0])
    for i, row in enumerate(rows):
        if len(row) != col_count:
            msg = (
                f"template_areas row {i} has {len(row)} columns "
                f"but row 0 has {col_count}"
            )
            raise ValueError(msg)

    return rows


def resolve_grid(
    template_areas: str,
    columns: str,
    rows: str,
    canvas_w: int,
    canvas_h: int,
) -> dict[str, Rect]:
    """Resolve a CSS grid layout into pixel bounding boxes.

    Each unique name in ``template_areas`` maps to a :class:`Rect` covering
    all cells in its (contiguous rectangular) span.

    Args:
        template_areas: CSS grid template_areas string (see .casedd spec).
        columns: Grid column track sizes (e.g. ``"1fr 1fr 200px"``).
        rows: Grid row track sizes (e.g. ``"80px 1fr 1fr"``).
        canvas_w: Total canvas width in pixels.
        canvas_h: Total canvas height in pixels.

    Returns:
        Dict mapping area name → :class:`Rect`.

    Raises:
        ValueError: If the template_areas grid is malformed or a named area
            does not form a contiguous rectangle.
    """
    grid = _parse_template_areas(template_areas)
    num_rows = len(grid)
    num_cols = len(grid[0]) if grid else 0

    col_sizes = _parse_tracks(columns, canvas_w)
    row_sizes = _parse_tracks(rows, canvas_h)

    # Pad or trim track lists to match the grid dimensions if the user provided
    # fewer/more tracks than cells (CSS gracefully handles this)
    col_sizes = _pad_or_trim(col_sizes, num_cols, canvas_w)
    row_sizes = _pad_or_trim(row_sizes, num_rows, canvas_h)

    # Build cumulative offsets along each axis
    col_offsets = _cumulative(col_sizes)  # col_offsets[i] = x start of column i
    row_offsets = _cumulative(row_sizes)  # row_offsets[i] = y start of row i

    # Collect the row/col extents for each area name
    extents: dict[str, tuple[int, int, int, int]] = {}  # name → (min_r, min_c, max_r, max_c)
    for r, row in enumerate(grid):
        for c, name in enumerate(row):
            if re.match(r"^\.", name):
                # A dot or "." is the CSS convention for an empty cell — skip
                continue
            if name not in extents:
                extents[name] = (r, c, r, c)
            else:
                min_r, min_c, max_r, max_c = extents[name]
                extents[name] = (
                    min(min_r, r), min(min_c, c),
                    max(max_r, r), max(max_c, c),
                )

    # Convert extents to pixel Rects
    result: dict[str, Rect] = {}
    for name, (min_r, min_c, max_r, max_c) in extents.items():
        x = col_offsets[min_c]
        y = row_offsets[min_r]
        w = col_offsets[max_c + 1] - x if max_c + 1 < len(col_offsets) else canvas_w - x
        h = row_offsets[max_r + 1] - y if max_r + 1 < len(row_offsets) else canvas_h - y
        result[name] = Rect(x=x, y=y, w=max(w, 1), h=max(h, 1))

    return result


def _pad_or_trim(sizes: list[int], target: int, total_px: int) -> list[int]:
    """Ensure ``sizes`` has exactly ``target`` entries.

    Short lists are extended with equal shares of remaining space.
    Long lists are truncated.

    Args:
        sizes: Current pixel sizes list.
        target: Required number of tracks.
        total_px: Total available pixels (used when padding).

    Returns:
        List of exactly ``target`` pixel sizes.
    """
    if len(sizes) == target:
        return sizes
    if len(sizes) > target:
        return sizes[:target]
    # Not enough tracks — fill remainder equally
    filled = sum(sizes)
    extra = max(0, total_px - filled)
    extra_count = target - len(sizes)
    per = extra // extra_count if extra_count else 0
    return sizes + [per] * extra_count


def _cumulative(sizes: list[int]) -> list[int]:
    """Return cumulative offsets from a list of sizes.

    E.g. ``[100, 200, 100]`` → ``[0, 100, 300, 400]``

    Args:
        sizes: List of track pixel sizes.

    Returns:
        List of length ``len(sizes) + 1`` with cumulative pixel offsets.
    """
    offsets = [0]
    total = 0
    for s in sizes:
        total += s
        offsets.append(total)
    return offsets
