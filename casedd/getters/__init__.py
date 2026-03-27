"""Data-source getter sub-package.

Each module implements a :class:`~casedd.getters.base.BaseGetter` subclass
that polls a system data source at a configurable interval and pushes values
into the shared :class:`~casedd.data_store.DataStore`.

Available getters:
    - :mod:`casedd.getters.cpu` — CPU usage, temperature, fan RPM
    - :mod:`casedd.getters.gpu` — NVIDIA GPU stats via ``nvidia-smi``
    - :mod:`casedd.getters.memory` — RAM usage
    - :mod:`casedd.getters.disk` — Disk usage
    - :mod:`casedd.getters.network` — Network byte rates
    - :mod:`casedd.getters.ollama` — Ollama API runtime state
    - :mod:`casedd.getters.speedtest` — Ookla speed test sampling
    - :mod:`casedd.getters.system` — Hostname, uptime, load average
"""
