"""Pydantic models for the .casedd template format.

These models are the canonical in-memory representation of a parsed template.
Every field is strictly typed. Unknown fields raise a ``ValidationError``.

Public API:
    - :class:`WidgetType` — enum of all supported widget type strings
    - :class:`ScaleMode` — image/slideshow scale modes
    - :class:`TransitionMode` — slideshow transition modes
    - :class:`LayoutDirection` — panel child layout direction
    - :class:`AlignMode` — panel child alignment
    - :class:`ColorStop` — (threshold, color) pair for gradient coloring
    - :class:`GridConfig` — CSS grid layout config
    - :class:`WidgetConfig` — a single widget's full configuration
    - :class:`Template` — the top-level parsed template model
"""

from __future__ import annotations

from enum import StrEnum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from casedd.config import RotationSkipCondition


class WidgetType(StrEnum):
    """All supported widget type identifiers."""

    PANEL = "panel"
    VALUE = "value"
    TEXT = "text"
    BAR = "bar"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SPARKLINE = "sparkline"
    IMAGE = "image"
    SLIDESHOW = "slideshow"
    CLOCK = "clock"
    UPS = "ups"
    HTOP = "htop"
    NET_PORTS = "net_ports"
    SYSINFO = "sysinfo"
    APOD = "apod"
    WEATHER_CONDITIONS = "weather_conditions"
    WEATHER_FORECAST = "weather_forecast"
    WEATHER_ALERTS = "weather_alerts"
    WEATHER_RADAR = "weather_radar"
    PLEX_NOW_PLAYING = "plex_now_playing"
    PLEX_RECENTLY_ADDED = "plex_recently_added"


class ScaleMode(StrEnum):
    """How an image is scaled to fit its bounding box."""

    FIT = "fit"       # maintain aspect ratio, letterbox
    FILL = "fill"     # maintain aspect ratio, crop
    STRETCH = "stretch"  # ignore aspect ratio, fill exactly


class TransitionMode(StrEnum):
    """Slideshow transition between images."""

    NONE = "none"
    FADE = "fade"


class LayoutDirection(StrEnum):
    """Panel child layout axis."""

    ROW = "row"
    COLUMN = "column"


class AlignMode(StrEnum):
    """Child alignment within a panel."""

    START = "start"
    CENTER = "center"
    END = "end"


class BorderStyle(StrEnum):
    """Border drawing styles for widget cells."""

    NONE = "none"
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    INSET = "inset"
    OUTSET = "outset"


class TemplateLayoutMode(StrEnum):
    """How a template layout maps into the active output canvas."""

    STRETCH = "stretch"
    FIT = "fit"


# A color stop is a 2-element list: [threshold, hex_color]
# We validate this as a tuple after parsing.
ColorStop = tuple[float, str]


class GridConfig(BaseModel):
    """CSS Grid layout configuration.

    Attributes:
        template_areas: Multi-line CSS grid template areas string.
        columns: Space-separated column track sizes (fr, px, %).
        rows: Space-separated row track sizes (fr, px, %).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_areas: str
    columns: str = "1fr"
    rows: str = "1fr"


class ImageTierCondition(BaseModel):
    """One condition for metric-driven image tier selection.

    The condition fires when the named data-store key satisfies the comparison.
    A missing key evaluates to ``False`` (the tier stays inactive).

    Attributes:
        source: Dotted data-store key to inspect (e.g. ``cpu.percent``).
        operator: Comparison operator; default ``gte`` (≥) suits threshold checks.
        value: Numeric or string threshold to compare against.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    operator: Literal["gt", "gte", "lt", "lte", "eq", "neq"] = "gte"
    value: float | int | str = 0.0


class ImageTier(BaseModel):
    """One image tier for metric-driven image selection.

    A tier is active when **any** condition in ``when`` fires (OR semantics).
    Tiers are listed in ascending order of severity; the highest-matching tier
    wins.  The base ``path`` on the parent widget is shown when no tier fires.

    Attributes:
        path: File path of the image to display when this tier is active.
        when: Conditions evaluated with OR semantics; one match activates the tier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    when: list[ImageTierCondition]


class WidgetConfig(BaseModel):
    """Configuration for a single widget.

    Widgets may contain ``children`` (for ``panel`` type) enabling unlimited
    nesting. All fields are optional except ``type``.

    Attributes:
        type: Widget type identifier.
        source: Dotted data store key for live data (e.g. ``cpu.temperature``).
        content: Literal static string (alternative to ``source``).
        label: Display label string.
        unit: Unit suffix for ``value`` widgets (e.g. ``"°C"``).
        precision: Decimal places for numeric values (default 0).
        color: Primary color (hex/rgb/named).
        background: Widget background color.
        font_size: Font size in points, or ``"auto"`` for bounding-box scaling.
        padding: Inner padding in pixels (int or [top, right, bottom, left]).
        min: Minimum value for ranged widgets (bar, gauge, histogram, sparkline).
        max: Maximum value for ranged widgets.
        color_stops: Gradient color stops as list of [threshold, color].
        path: File path for image widget.
        tiers: Metric-driven image tiers for the image widget.  Listed in
            ascending severity; the highest matching tier's image is shown.
            Falls back to ``path`` when no tier fires.
        paths: List of file/dir paths for slideshow widget.
        scale: Image scale mode.
        interval: Slideshow seconds per image.
        transition: Slideshow transition effect.
        samples: History length for histogram/sparkline.
        window_seconds: Optional time window for histogram/sparkline history.
        sources: Optional list of source keys for multi-series histogram mode.
        series_labels: Optional labels for each source series.
        series_colors: Optional colors for each source series.
        format: strftime format string for clock widget.
        direction: Panel child layout direction.
        align: Panel child alignment.
        gap: Pixels between panel children.
        width: Explicit width override in pixels (for inline panel children).
        height: Explicit height override in pixels (for inline panel children).
        children: Inline child widgets (panel with direction layout).
        grid: Nested grid config (panel alternative to direction layout).
        children_named: Named child widgets for nested grid panels.
        arc_start: Gauge arc start angle in degrees.
        arc_end: Gauge arc end angle in degrees.
        gauge_ticks: Number of tick marks to draw along a gauge arc.
        sort_key: Sort column for htop widget ("cpu" or "mem").
        filter_regex: Optional Python regex used to hide matching rows for
            table-like widgets (htop and Plex list widgets).
        max_items: Optional row cap for list-like widgets (htop and Plex
            tables). When unset, widgets render as many rows as fit.
        border_style: Widget border style (none/solid/dashed/dotted/inset/outset).
        border_color: Border color string.
        border_width: Border line width in pixels.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: WidgetType

    # Data source (one of these should be set, but both optional to allow bare panels)
    source: str | None = None
    content: str | None = None

    # Display
    label: str | None = None
    unit: str | None = None
    precision: int = Field(default=0, ge=0, le=6)
    color: str | None = None
    background: str | None = None
    # font_size is int or "auto"
    font_size: int | str = Field(default="auto")
    padding: int | list[int] = Field(default=0)

    # Ranged widgets (bar, gauge, histogram, sparkline)
    min: float = 0.0
    max: float = 100.0
    color_stops: list[list[float | str]] = Field(default_factory=list)

    # Image widget
    path: str | None = None
    tiers: list[ImageTier] = Field(default_factory=list)
    scale: ScaleMode = ScaleMode.FIT
    zoom: float = Field(default=1.0, ge=1.0, le=8.0)

    # Slideshow widget
    paths: list[str] = Field(default_factory=list)
    interval: float = Field(default=10.0, gt=0)
    transition: TransitionMode = TransitionMode.NONE

    # Histogram / sparkline
    samples: int = Field(default=60, gt=0)
    window_seconds: float | None = Field(default=None, gt=0)
    sources: list[str] = Field(default_factory=list)
    series_labels: list[str] = Field(default_factory=list)
    series_colors: list[str] = Field(default_factory=list)

    # Clock widget
    format: str = "%H:%M:%S"

    # Panel (direction layout)
    direction: LayoutDirection = LayoutDirection.COLUMN
    align: AlignMode = AlignMode.START
    gap: int = Field(default=0, ge=0)
    width: int | None = None
    height: int | None = None
    children: list[WidgetConfig] = Field(default_factory=list)

    # Panel (grid layout — alternative to direction)
    grid: GridConfig | None = None
    children_named: dict[str, WidgetConfig] = Field(default_factory=dict)

    # Gauge-specific
    arc_start: float = 225.0
    arc_end: float = -45.0
    gauge_ticks: int = Field(default=0, ge=0, le=20)

    # Htop process table
    sort_key: str = Field(default="cpu")
    filter_regex: str | None = Field(default=None)
    max_items: int | None = Field(default=None, ge=1, le=200)

    # Widget border
    border_style: BorderStyle = BorderStyle.NONE
    border_color: str | None = None
    border_width: int = Field(default=1, ge=1, le=16)

    @field_validator("filter_regex")
    @classmethod
    def _validate_filter_regex(cls, value: str | None) -> str | None:
        """Compile-check filter_regex to catch broken patterns at template load time.

        Args:
            value: Raw regex string or None.

        Returns:
            The validated regex string or None.

        Raises:
            ValueError: If the pattern is not a valid Python regex.
        """
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                msg = f"filter_regex is not a valid regex: {exc}"
                raise ValueError(msg) from exc
        return value

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_widget_type_alias(cls, value: object) -> object:
        """Normalize legacy/alias widget type names before enum parsing.

        Args:
            value: Raw type field value.

        Returns:
            Normalized widget type token.
        """
        if isinstance(value, str) and value.strip().lower() == "power.ups":
            return "ups"
        return value

    @field_validator("border_style", mode="before")
    @classmethod
    def _normalize_border_style(cls, value: object) -> object:
        """Normalize common border-style aliases before enum parsing."""
        if isinstance(value, str) and value.strip().lower() == "outsed":
            return "outset"
        return value

    @model_validator(mode="after")
    def _check_font_size(self) -> WidgetConfig:
        """Validate that font_size is either a positive int or the string 'auto'.

        Returns:
            Self after validation.

        Raises:
            ValueError: If font_size is an invalid string or non-positive int.
        """
        fs = self.font_size
        if isinstance(fs, str) and fs != "auto":
            msg = f"font_size must be a positive integer or 'auto', got '{fs}'"
            raise ValueError(msg)
        if isinstance(fs, int) and fs <= 0:
            msg = f"font_size must be positive, got {fs}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_panel_layout(self) -> WidgetConfig:
        """Ensure a panel widget doesn't specify both grid and direction children.

        Returns:
            Self after validation.

        Raises:
            ValueError: If both ``children`` and ``children_named`` + ``grid`` are set.
        """
        if self.children and (self.grid or self.children_named):
            msg = (
                "panel: use either 'children' (direction layout) "
                "or 'grid'+'children_named', not both"
            )
            raise ValueError(msg)
        return self


class Template(BaseModel):
    """Top-level parsed template model.

    Attributes:
        name: Unique template name.
        description: Human-readable description.
        width: Optional legacy design width in pixels.
        height: Optional legacy design height in pixels.
        aspect_ratio: Optional logical layout aspect ratio, e.g. ``"5:3"``.
        layout_mode: Whether the layout stretches to the full output or fits
            inside it while preserving aspect ratio.
        background: Canvas background color.
        refresh_rate: Override render rate in Hz (``None`` uses daemon default).
        grid: Top-level CSS grid layout.
        widgets: Named widget definitions corresponding to grid area names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    aspect_ratio: str | None = None
    layout_mode: TemplateLayoutMode = TemplateLayoutMode.STRETCH
    background: str = "#000000"
    refresh_rate: float | None = Field(default=None, gt=0)
    grid: GridConfig
    widgets: dict[str, WidgetConfig]
    # Template-level skip conditions.  Used as fallback when a rotation entry
    # for this template has no entry-level skip_if.  Rotation-level conditions
    # always take priority over these template-level defaults.
    skip_if: list[RotationSkipCondition] = Field(default_factory=list)

    @field_validator("aspect_ratio")
    @classmethod
    def _validate_aspect_ratio(cls, value: str | None) -> str | None:
        """Validate optional top-level aspect ratio syntax."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if ":" in cleaned:
            parts = cleaned.split(":", maxsplit=1)
            try:
                left = float(parts[0])
                right = float(parts[1])
            except ValueError as exc:
                raise ValueError("aspect_ratio must look like '5:3' or '1.777'") from exc
            if left <= 0 or right <= 0:
                raise ValueError("aspect_ratio values must be > 0")
            return cleaned
        try:
            ratio = float(cleaned)
        except ValueError as exc:
            raise ValueError("aspect_ratio must look like '5:3' or '1.777'") from exc
        if ratio <= 0:
            raise ValueError("aspect_ratio must be > 0")
        return cleaned

    @model_validator(mode="after")
    def _check_widget_names(self) -> Template:
        """Verify every name in template_areas has a matching widget definition.

        Returns:
            Self after validation.

        Raises:
            ValueError: If any grid area name lacks a widget definition.
        """
        # Extract all area names from the template_areas string
        area_names: set[str] = set(
            re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", self.grid.template_areas)
        )
        missing = area_names - set(self.widgets.keys())
        if missing:
            missing_sorted = sorted(missing)
            msg = (
                f"Template '{self.name}': grid areas have no widget definition: "
                f"{missing_sorted}"
            )
            raise ValueError(msg)
        return self
