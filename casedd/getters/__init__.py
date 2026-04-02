"""Data-source getter sub-package.

Each module implements a :class:`~casedd.getters.base.BaseGetter` subclass
that polls a system data source at a configurable interval and pushes values
into the shared :class:`~casedd.data_store.DataStore`.

Available getters:
    - :mod:`casedd.getters.containers` — Docker/Podman/containerd status
    - :mod:`casedd.getters.cpu` — CPU usage, temperature, fan RPM
    - :mod:`casedd.getters.fans` — system/CPU/GPU fan telemetry
    - :mod:`casedd.getters.gpu` — NVIDIA GPU stats via ``nvidia-smi``
    - :mod:`casedd.getters.htop` — htop-style process list by CPU usage
    - :mod:`casedd.getters.invokeai` — InvokeAI queue/runtime telemetry
    - :mod:`casedd.getters.memory` — RAM usage
    - :mod:`casedd.getters.disk` — Disk usage
    - :mod:`casedd.getters.network` — Network byte rates
    - :mod:`casedd.getters.nzbget` — NZBGet downloader queue and history
    - :mod:`casedd.getters.os_updates` — OS package update/security status
    - :mod:`casedd.getters.ollama` — Ollama API runtime state
    - :mod:`casedd.getters.pihole` — Pi-hole DNS filtering/query telemetry
    - :mod:`casedd.getters.plex` — Plex server/session/library telemetry
    - :mod:`casedd.getters.servarr` — Radarr/Sonarr queue/health/disk telemetry
    - :mod:`casedd.getters.speedtest` — Ookla speed test sampling
    - :mod:`casedd.getters.system` — Hostname, uptime, load average
    - :mod:`casedd.getters.ups` — UPS metrics via apcaccess/upsc/custom command
    - :mod:`casedd.getters.vms` — KVM/libvirt VM telemetry via ``virsh``
    - :mod:`casedd.getters.weather` — NWS/open-meteo weather + alert telemetry
"""
