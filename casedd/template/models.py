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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    scale: ScaleMode = ScaleMode.FIT

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

    # Widget border
    border_style: BorderStyle = BorderStyle.NONE
    border_color: str | None = None
    border_width: int = Field(default=1, ge=1, le=16)

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
        width: Canvas width in pixels.
        height: Canvas height in pixels.
        background: Canvas background color.
        refresh_rate: Override render rate in Hz (``None`` uses daemon default).
        grid: Top-level CSS grid layout.
        widgets: Named widget definitions corresponding to grid area names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    width: int = Field(default=800, gt=0)
    height: int = Field(default=480, gt=0)
    background: str = "#000000"
    refresh_rate: float | None = Field(default=None, gt=0)
    grid: GridConfig
    widgets: dict[str, WidgetConfig]

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
