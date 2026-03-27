"""Template engine sub-package for CASEDD.

Parses ``.casedd`` YAML template files into a validated widget tree model,
resolves the CSS Grid Template Areas layout into pixel bounding boxes, and
registers templates by name for hot-reload.

Sub-modules:
    - :mod:`casedd.template.models` — Pydantic models for the widget tree
    - :mod:`casedd.template.loader` — YAML → :class:`~casedd.template.models.Template`
    - :mod:`casedd.template.grid` — CSS grid area → pixel rect solver
    - :mod:`casedd.template.registry` — template name → loaded template, hot-reload
"""
