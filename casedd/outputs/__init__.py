"""Output sink sub-package for CASEDD.

Modules:
    - :mod:`casedd.outputs.base` — abstract :class:`OutputBackend` base class
    - :mod:`casedd.outputs.registry` — :class:`OutputRegistry` factory
    - :mod:`casedd.outputs.framebuffer` — mmap write to /dev/fb1
    - :mod:`casedd.outputs.websocket` — FastAPI WebSocket broadcast
    - :mod:`casedd.outputs.http_viewer` — FastAPI HTTP app (web viewer + API)
"""
