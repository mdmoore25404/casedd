"""Widget renderer sub-package.

Each module in this package implements one or more widget types by providing
a ``draw(img, rect, cfg, data)`` function that paints directly onto a PIL
Image.

Modules:
    - :mod:`casedd.renderer.widgets.base` — abstract base + shared helpers
    - :mod:`casedd.renderer.widgets.boolean` — boolean status icon display
    - :mod:`casedd.renderer.widgets.panel` — container widget
    - :mod:`casedd.renderer.widgets.value` — numeric value display
    - :mod:`casedd.renderer.widgets.text` — string display
    - :mod:`casedd.renderer.widgets.bar` — horizontal progress bar
    - :mod:`casedd.renderer.widgets.gauge` — tachometer arc gauge
    - :mod:`casedd.renderer.widgets.histogram` — rolling bar chart
    - :mod:`casedd.renderer.widgets.sparkline` — rolling line chart
    - :mod:`casedd.renderer.widgets.image` — static image display
    - :mod:`casedd.renderer.widgets.slideshow` — cycling image display
    - :mod:`casedd.renderer.widgets.clock` — live clock
    - :mod:`casedd.renderer.widgets.ups` — single-card UPS status display
"""
