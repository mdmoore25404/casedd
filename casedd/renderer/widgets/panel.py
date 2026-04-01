"""Panel widget renderer.

A container that lays out child widgets either:
- In a row or column (``direction`` layout), or
- Using a nested CSS grid (``grid`` + ``children_named``).

Panels may be nested to arbitrary depth, enabling complex compositions such
as a gauge + value label stacked beneath it inside a single grid cell.

Example .casedd config:

.. code-block:: yaml

    cpu:
      type: panel
      background: "#1e1e3f"
      direction: column
      gap: 4
      children:
        - type: gauge
          source: cpu.percent
          label: "CPU"
        - type: value
          source: cpu.temperature
          unit: "°C"
          font_size: auto
"""

from __future__ import annotations

import logging

from PIL import Image

from casedd.data_store import DataStore
from casedd.renderer.widgets.base import BaseWidget, draw_widget_border, fill_background
from casedd.template.grid import Rect, resolve_grid
from casedd.template.models import LayoutDirection, WidgetConfig

_log = logging.getLogger(__name__)


class PanelWidget(BaseWidget):
    """Container widget that renders child widgets within its bounding box."""

    def draw(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Lay out and render all children into the panel's bounding box.

        Args:
            img: Canvas image.
            rect: Panel's bounding box.
            cfg: Panel widget configuration.
            data: Live data store.
            state: Mutable state dict; child states are nested under child index keys.
        """
        fill_background(img, rect, cfg.background)

        if cfg.grid is not None and cfg.children_named:
            self._draw_grid_children(img, rect, cfg, data, state)
        elif cfg.children:
            self._draw_direction_children(img, rect, cfg, data, state)

    def _draw_direction_children(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Lay out children along a single axis (row or column).

        Args:
            img: Canvas image.
            rect: Panel bounding box.
            cfg: Panel configuration.
            data: Live data store.
            state: Mutable state dict.
        """
        # registry is imported here to break the panel ↔ registry circular dependency.
        # This is the ONE documented exception to the no-local-imports rule.
        # See casedd/renderer/widgets/registry.py for the full explanation.
        from casedd.renderer.widgets.registry import get_widget_renderer  # noqa: PLC0415

        n = len(cfg.children)
        if n == 0:
            return

        is_row = cfg.direction == LayoutDirection.ROW
        total = rect.w if is_row else rect.h
        gap_total = cfg.gap * (n - 1)
        child_size = max(1, (total - gap_total) // n)

        for i, child_cfg in enumerate(cfg.children):
            # Honour explicit override size if the child specifies one
            override = (child_cfg.width if is_row else child_cfg.height) or child_size
            offset = sum(
                (
                    (cfg.children[j].width or child_size)
                    if is_row
                    else (cfg.children[j].height or child_size)
                ) + cfg.gap
                for j in range(i)
            )
            if is_row:
                child_rect = Rect(
                    x=rect.x + offset, y=rect.y,
                    w=override, h=rect.h,
                )
            else:
                child_rect = Rect(
                    x=rect.x, y=rect.y + offset,
                    w=rect.w, h=override,
                )

            child_state_key = f"child_{i}"
            if child_state_key not in state:
                state[child_state_key] = {}
            child_state: dict[str, object] = state[child_state_key]  # type: ignore[assignment]

            renderer = get_widget_renderer(child_cfg.type)
            renderer.draw(img, child_rect, child_cfg, data, child_state)
            draw_widget_border(img, child_rect, child_cfg)

    def _draw_grid_children(
        self,
        img: Image.Image,
        rect: Rect,
        cfg: WidgetConfig,
        data: DataStore,
        state: dict[str, object],
    ) -> None:
        """Lay out named children using a nested CSS grid.

        Args:
            img: Canvas image.
            rect: Panel bounding box.
            cfg: Panel configuration (must have ``cfg.grid`` set).
            data: Live data store.
            state: Mutable state dict.
        """
        # See note in _draw_direction_children re: this local import exception.
        from casedd.renderer.widgets.registry import get_widget_renderer  # noqa: PLC0415

        assert cfg.grid is not None  # guarded by caller

        grid_cache_key = (
            cfg.grid.template_areas,
            cfg.grid.columns,
            cfg.grid.rows,
            rect.w,
            rect.h,
        )
        cached_key = state.get("panel_grid_cache_key")
        cached_rects = state.get("panel_grid_cache_rects")
        if cached_key == grid_cache_key and isinstance(cached_rects, dict):
            rects = cached_rects
        else:
            rects = resolve_grid(
                template_areas=cfg.grid.template_areas,
                columns=cfg.grid.columns,
                rows=cfg.grid.rows,
                canvas_w=rect.w,
                canvas_h=rect.h,
            )
            state["panel_grid_cache_key"] = grid_cache_key
            state["panel_grid_cache_rects"] = rects

        for name, child_cfg in cfg.children_named.items():
            child_local_rect = rects.get(name)
            if child_local_rect is None:
                _log.warning("Panel: child '%s' not found in nested grid areas.", name)
                continue
            # Convert local-rect to canvas-absolute coordinates
            child_rect = Rect(
                x=rect.x + child_local_rect.x,
                y=rect.y + child_local_rect.y,
                w=child_local_rect.w,
                h=child_local_rect.h,
            )
            child_state_key = f"named_{name}"
            if child_state_key not in state:
                state[child_state_key] = {}
            child_state: dict[str, object] = state[child_state_key]  # type: ignore[assignment]

            renderer = get_widget_renderer(child_cfg.type)
            renderer.draw(img, child_rect, child_cfg, data, child_state)
            draw_widget_border(img, child_rect, child_cfg)
