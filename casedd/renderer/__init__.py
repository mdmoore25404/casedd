"""Image renderer sub-package for CASEDD.

Takes a parsed :class:`~casedd.template.models.Template` and a
:class:`~casedd.data_store.DataStore` snapshot and produces a
:class:`PIL.Image.Image` ready for output.

Sub-modules:
    - :mod:`casedd.renderer.engine` — top-level render function
    - :mod:`casedd.renderer.color` — color parsing and color_stops interpolation
    - :mod:`casedd.renderer.fonts` — font loading and auto-scaling
    - :mod:`casedd.renderer.widgets` — one module per widget type
"""
