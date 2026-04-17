"""Widget type registry.

Maps :class:`~casedd.template.models.WidgetType` values to their
:class:`~casedd.renderer.widgets.base.BaseWidget` renderer instances.

This module is the single source of truth for widget type → renderer
mapping, imported by both the render engine and the panel widget to avoid
circular imports.

Public API:
    - :func:`get_widget_renderer` — look up the renderer for a widget type
"""

from __future__ import annotations

from casedd.renderer.widgets.apod import ApodWidget
from casedd.renderer.widgets.bar import BarWidget
from casedd.renderer.widgets.base import BaseWidget
from casedd.renderer.widgets.boolean import BooleanWidget
from casedd.renderer.widgets.clock import ClockWidget
from casedd.renderer.widgets.gauge import GaugeWidget
from casedd.renderer.widgets.histogram import HistogramWidget
from casedd.renderer.widgets.htop import HtopWidget
from casedd.renderer.widgets.image import ImageWidget
from casedd.renderer.widgets.jellyfin_now_playing import JellyfinNowPlayingWidget
from casedd.renderer.widgets.net_ports import NetPortsWidget
from casedd.renderer.widgets.ollama import OllamaWidget
from casedd.renderer.widgets.plex_now_playing import PlexNowPlayingWidget
from casedd.renderer.widgets.plex_recently_added import PlexRecentlyAddedWidget
from casedd.renderer.widgets.slideshow import SlideshowWidget
from casedd.renderer.widgets.sparkline import SparklineWidget
from casedd.renderer.widgets.sysinfo import SysinfoWidget
from casedd.renderer.widgets.table import TableWidget
from casedd.renderer.widgets.text import TextWidget
from casedd.renderer.widgets.ups import UpsWidget
from casedd.renderer.widgets.value import ValueWidget
from casedd.renderer.widgets.weather_alerts import WeatherAlertsWidget
from casedd.renderer.widgets.weather_conditions import WeatherConditionsWidget
from casedd.renderer.widgets.weather_forecast import WeatherForecastWidget
from casedd.renderer.widgets.weather_radar import WeatherRadarWidget
from casedd.template.models import WidgetType

# Instantiated once; all renderers are stateless — state lives in the engine's
# per-widget state dicts, not in the renderer instances.
# PanelWidget is imported lazily by get_widget_renderer to avoid the circular
# import that would arise from panel.py importing this module at parse time.
_REGISTRY: dict[WidgetType, BaseWidget] = {
    WidgetType.BOOLEAN: BooleanWidget(),
    WidgetType.VALUE: ValueWidget(),
    WidgetType.TEXT: TextWidget(),
    WidgetType.TABLE: TableWidget(),
    WidgetType.BAR: BarWidget(),
    WidgetType.GAUGE: GaugeWidget(),
    WidgetType.HISTOGRAM: HistogramWidget(),
    WidgetType.SPARKLINE: SparklineWidget(),
    WidgetType.IMAGE: ImageWidget(),
    WidgetType.SLIDESHOW: SlideshowWidget(),
    WidgetType.CLOCK: ClockWidget(),
    WidgetType.UPS: UpsWidget(),
    WidgetType.HTOP: HtopWidget(),
    WidgetType.NET_PORTS: NetPortsWidget(),
    WidgetType.SYSINFO: SysinfoWidget(),
    WidgetType.APOD: ApodWidget(),
    WidgetType.WEATHER_CONDITIONS: WeatherConditionsWidget(),
    WidgetType.WEATHER_FORECAST: WeatherForecastWidget(),
    WidgetType.WEATHER_ALERTS: WeatherAlertsWidget(),
    WidgetType.WEATHER_RADAR: WeatherRadarWidget(),
    WidgetType.PLEX_NOW_PLAYING: PlexNowPlayingWidget(),
    WidgetType.PLEX_RECENTLY_ADDED: PlexRecentlyAddedWidget(),
    WidgetType.JELLYFIN_NOW_PLAYING: JellyfinNowPlayingWidget(),
    WidgetType.OLLAMA: OllamaWidget(),
}


def get_widget_renderer(widget_type: WidgetType) -> BaseWidget:
    """Return the renderer instance for a given widget type.

    The ``panel`` type is handled specially: it is instantiated on first
    access to avoid a module-level circular import
    (PanelWidget → registry → PanelWidget).

    Args:
        widget_type: The :class:`~casedd.template.models.WidgetType` to look up.

    Returns:
        The :class:`~casedd.renderer.widgets.base.BaseWidget` renderer.

    Raises:
        KeyError: If ``widget_type`` has no registered renderer.
    """
    if widget_type == WidgetType.PANEL:
        # Import panel here to break the circular dependency at module-load time.
        # This is the ONE permitted exception to the no-local-imports rule,
        # documented here: panel.py ↔ registry.py is an unavoidable cycle because
        # panels contain child widgets of any type including other panels.
        from casedd.renderer.widgets.panel import PanelWidget  # noqa: PLC0415

        if WidgetType.PANEL not in _REGISTRY:
            _REGISTRY[WidgetType.PANEL] = PanelWidget()
        return _REGISTRY[WidgetType.PANEL]

    return _REGISTRY[widget_type]
