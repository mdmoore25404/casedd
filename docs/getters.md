---
title: Getter Reference
nav_order: 3
permalink: /getters/
---

# Built-in Getters and Keys

Welcome to the CASEDD getter docs. This page describes all built-in system collectors and the consistent dotted key namespace they expose to the renderer engine.

## Key naming conventions

- Getter-produced values use dotted keys like cpu.percent, nvidia.temperature, speedtest.download_mbps.
- Keys are flat primitives (float, int, str), not nested JSON objects.

## CPU getter

Module: casedd/getters/cpu.py

Config:
- `CASEDD_CPU_INTERVAL` — poll interval in seconds (default: `2.0`)
- `cpu_interval` in `casedd.yaml`

Emits:
- cpu.percent
- cpu.temperature
- cpu.fan_rpm

Notes:
- Temperature keys depend on host sensors (coretemp/k10temp/acpitz/cpu_thermal).
- cpu.fan_rpm remains for compatibility; richer fan telemetry is emitted by fan getter.

## Fan getter

Module: casedd/getters/fans.py

Config:
- `CASEDD_FANS_INTERVAL` — poll interval in seconds (default: `3.0`)
- `fans_interval` in `casedd.yaml`

Emits aggregate keys:
- fans.total.count
- fans.cpu.count
- fans.cpu.avg_rpm
- fans.cpu.max_rpm
- fans.system.count
- fans.system.avg_rpm
- fans.system.max_rpm
- fans.gpu.count
- fans.gpu.avg_rpm
- fans.gpu.max_rpm

Emits per-fan keys:
- fans.cpu.0.rpm, fans.cpu.1.rpm, ...
- fans.system.0.rpm, fans.system.1.rpm, ...
- fans.gpu.0.rpm, fans.gpu.1.rpm, ...

Compatibility:
- cpu.fan_rpm is mirrored from fans.cpu.max_rpm when fan getter runs.

Notes:
- CPU/system fans come from psutil.sensors_fans where available.
- GPU fans use psutil when exposed, plus nvidia-smi fan.speed percentages when available.

## GPU getter (NVIDIA via nvidia-smi)

Module: casedd/getters/gpu.py

Config:
- `CASEDD_GPU_INTERVAL` — poll interval in seconds (default: `5.0`)
- `gpu_interval` in `casedd.yaml`

Emits backward-compatible primary keys (GPU 0 when present):
- nvidia.name
- nvidia.percent
- nvidia.temperature
- nvidia.memory_used_mb
- nvidia.memory_free_mb
- nvidia.memory_total_mb
- nvidia.memory_percent
- nvidia.power_w

Emits multi-GPU keys when multiple GPUs are present:
- nvidia.gpu_count
- nvidia.0.name, nvidia.0.percent, nvidia.0.temperature, nvidia.0.memory_used_mb, nvidia.0.memory_free_mb, nvidia.0.memory_total_mb, nvidia.0.power_w
- nvidia.1.name, nvidia.1.percent, nvidia.1.temperature, nvidia.1.memory_used_mb, nvidia.1.memory_free_mb, nvidia.1.memory_total_mb, nvidia.1.power_w
- etc. for each GPU index
- nvidia.total_memory_used_mb
- nvidia.total_memory_free_mb
- nvidia.total_memory_mb

nvidia-smi fields currently queried:
- index
- name
- utilization.gpu
- temperature.gpu
- memory.used
- memory.total
- power.draw

Notes derived (not directly from nvidia-smi):
- `nvidia.memory_free_mb` — computed as `memory.total - memory.used`
- `nvidia.memory_percent` — computed as `memory.used / memory.total * 100`

Notes:
- If nvidia-smi is absent, the getter disables itself gracefully.

## Memory getter

Module: casedd/getters/memory.py

Config:
- `CASEDD_MEMORY_INTERVAL` — poll interval in seconds (default: `2.0`)
- `memory_interval` in `casedd.yaml`

Emits:
- memory.percent
- memory.used_gb
- memory.total_gb
- memory.available_gb

## Disk getter

Module: casedd/getters/disk.py

Config:
- `CASEDD_DISK_INTERVAL` — poll interval in seconds (default: `2.0`)
- `disk_interval` in `casedd.yaml`

Emits:
- disk.percent
- disk.used_gb
- disk.total_gb
- disk.free_gb
- disk.read_mb_s
- disk.write_mb_s
- disk.read_mbps (legacy compatibility alias; value is MB/s)
- disk.write_mbps (legacy compatibility alias; value is MB/s)

## Network getter

Module: casedd/getters/network.py

Config:
- `CASEDD_NETWORK_INTERVAL` — poll interval in seconds (default: `2.0`)
- `CASEDD_NET_INTERFACES` — comma-separated list of NIC names to monitor
- `network_interval` in `casedd.yaml`

Emits:
- net.bytes_recv_rate
- net.bytes_sent_rate
- net.recv_mbps
- net.sent_mbps
- net.bytes_recv_total
- net.bytes_sent_total

## System getter

Module: casedd/getters/system.py

Config:
- `CASEDD_SYSTEM_INTERVAL` — poll interval in seconds (default: `10.0`)
- `system_interval` in `casedd.yaml`

Emits:
- system.hostname
- system.uptime
- system.load_1
- system.load_5
- system.load_15
- system.boot_time

## Containers getter (Docker / Podman / containerd)

Module: casedd/getters/containers.py

Emits:
- containers.available
- containers.runtime
- containers.logo_path
- containers.count_total
- containers.count_running
- containers.count_exited
- containers.count_paused
- containers.rows
- containers.1.name, containers.1.status, containers.1.uptime, containers.1.health, containers.1.image
- containers.2.name, containers.2.status, ... up to configured max items

Display note:
- Container table rows render `Health <state>`.
- `Health unknown` means the runtime did not expose an explicit health check state
    for that container.

Runtime selection:
- auto (default): Docker first, then Podman, then containerd (`ctr`)
- docker
- podman
- containerd

Permission note:
- The CASEDD daemon user must be allowed to query the selected runtime.
- For Docker this usually means running as root or adding the daemon user to the `docker` group.
- For Podman rootless setups, run CASEDD as the same user that owns the Podman session.

## UPS getter

Module: casedd/getters/ups.py

Emits:
- ups.status
- ups.battery_percent
- ups.load_percent
- ups.load_watts
- ups.runtime_minutes
- ups.input_voltage
- ups.input_frequency
- ups.last_change_ts
- ups.online
- ups.on_battery
- ups.low_battery
- ups.charging
- ups.present

Backend preference:
- CASEDD_UPS_COMMAND (custom command)
- apcaccess -u
- upsc <target>

Notes:
- If no backend is available, getter remains alive and publishes `ups.status=unavailable` with `ups.present=0`.
- External pushes can write nested UPS payloads (for example `{\"update\": {\"ups\": {\"battery_percent\": 64}}}`) and they are flattened to dotted keys.

## Speedtest getter (Ookla CLI)

Module: casedd/getters/speedtest.py

Default interval: 1800s (30 min)

Optional env override:
- CASEDD_SPEEDTEST_SERVER_ID (forces Ookla target server ID instead of auto-select)
- CASEDD_SPEEDTEST_REFERENCE_DOWN_MBPS (optional host-local downlink baseline)
- CASEDD_SPEEDTEST_REFERENCE_UP_MBPS (optional host-local uplink baseline)

Emits:
- speedtest.download_mbps
- speedtest.upload_mbps
- speedtest.ping_ms
- speedtest.jitter_ms
- speedtest.download_pct_adv
- speedtest.upload_pct_adv
- speedtest.download_pct_ref
- speedtest.upload_pct_ref
- speedtest.download_status
- speedtest.upload_status
- speedtest.threshold_marginal_pct
- speedtest.threshold_critical_pct
- speedtest.last_run
- speedtest.summary
- speedtest.simple_summary
- speedtest.compact_summary
- speedtest.server_id
- speedtest.server_name
- speedtest.server_location
- speedtest.server_country
- speedtest.server_host

## OS package updates getter

Module: casedd/getters/os_updates.py

Supported managers:
- apt (Debian/Ubuntu/Mint)
- dnf (Fedora/RHEL)

Config:
- CASEDD_OS_UPDATES_INTERVAL (default: 900 seconds)
- CASEDD_OS_UPDATES_MANAGER (auto|apt|dnf, default: auto)

Emits:
- os_updates.manager
- os_updates.active
- os_updates.total_count
- os_updates.security_count
- os_updates.has_updates
- os_updates.has_security_updates
- os_updates.phased_count
- os_updates.has_phased_updates
- os_updates.actionable_count
- os_updates.has_actionable_updates
- os_updates.rows
- os_updates.summary

Notes:
- Security classification is best-effort.
- apt uses channel hints like `*-security` from `apt list --upgradable`.
- apt phasing is detected from `apt -s upgrade` deferred/phasing output when available.
- dnf enriches security rows using `dnf updateinfo list security --updates` when available.
- `os_updates.rows` renders package rows in `name|version` format and may append
  `[SEC]` and `(phasing)` markers.
- `os_updates.actionable_count` counts updates that are both applicable and not phased-deferred.

## Ollama API getter

Module: casedd/getters/ollama.py

Config:
- `CASEDD_OLLAMA_API_BASE` — Ollama API base URL (default: `http://localhost:11434`)
- `CASEDD_OLLAMA_INTERVAL` — poll interval in seconds (default: `10.0`)
- `CASEDD_OLLAMA_TIMEOUT` — HTTP timeout in seconds (default: `3.0`)
- `CASEDD_OLLAMA_DETAILED` — `1` to enable per-model detail keys (default: `0`)
- `CASEDD_OLLAMA_DETAIL_MAX_MODELS` — max per-model entries emitted (default: `8`, max: `100`)

Emits:
- ollama.version
- ollama.active_count
- ollama.active_models
- ollama.active_compact
- ollama.primary_model
- ollama.primary_size_gb
- ollama.primary_gpu_percent
- ollama.primary_cpu_percent
- ollama.primary_ttl
- ollama.summary
- ollama.models.local_count
- ollama.models.running_count
- ollama.models.rows
- ollama.running.rows

When `CASEDD_OLLAMA_DETAILED=1`, per-model detail keys are also emitted (up to `CASEDD_OLLAMA_DETAIL_MAX_MODELS`):
- ollama.running_1.name, ollama.running_1.size_bytes, ollama.running_1.size_vram_bytes
- ollama.running_1.expires_at, ollama.running_1.ttl
- ollama.running_1.family, ollama.running_1.parameter_size, ollama.running_1.quantization_level
- ollama.model_1.name, ollama.model_1.modified_at, ollama.model_1.size_bytes
- ollama.model_1.family, ollama.model_1.parameter_size, ollama.model_1.quantization_level
- ... continued up to configured max

Notes:
- This getter uses the HTTP API only and does not require the `ollama` command.
- CPU/GPU percentages are parsed from optional processor text when present in API payload.
- Per-request live token/sec telemetry is intentionally not exposed because
  Ollama does not publish that as a pull-based metric endpoint.

## Servarr getters (Radarr / Sonarr)

Module: casedd/getters/servarr.py

Supported in this iteration:
- Radarr
- Sonarr

Per-app emits:
- radarr.active / sonarr.active
- radarr.queue.total / sonarr.queue.total
- radarr.queue.rows / sonarr.queue.rows
- radarr.health.warning_count / sonarr.health.warning_count
- radarr.health.error_count / sonarr.health.error_count
- radarr.calendar.upcoming_count / sonarr.calendar.upcoming_count
- radarr.disk.free_gb / sonarr.disk.free_gb

Aggregate emits:
- servarr.queue.total
- servarr.health.warning_count
- servarr.health.error_count
- servarr.rows
- servarr.radarr.rows
- servarr.sonarr.rows
- servarr.totals.rows

Config (one app):
- Set CASEDD_RADARR_BASE_URL and CASEDD_RADARR_API_KEY.
- Leave Sonarr vars blank to keep Sonarr inactive.

Config (two apps):
- Set both Radarr and Sonarr base URL + API key pairs.
- Optional tuning vars per app:
    - CASEDD_<APP>_INTERVAL
    - CASEDD_<APP>_TIMEOUT
    - CASEDD_<APP>_CALENDAR_DAYS
    - CASEDD_<APP>_VERIFY_TLS

Notes:
- Missing base URL or API key keeps that app inactive (no hard failure).
- 401/403 auth failures are surfaced as getter errors for health visibility.
- 5xx responses are surfaced as server errors for health visibility.

## Plex getter

Module: casedd/getters/plex.py

API references:
- https://developer.plex.tv/
- https://developer.plex.tv/pms/

Auth and headers:
- Uses `X-Plex-Token` for auth when configured.
- Sends `X-Plex-Client-Identifier` and `X-Plex-Product` for compatibility with Plex API guidance.

Primary emits:
- plex.server.name
- plex.server.version
- plex.server.platform
- plex.server.reachable
- plex.sessions.active_count
- plex.sessions.transcoding_count
- plex.sessions.direct_play_count
- plex.sessions.direct_stream_count
- plex.bandwidth.current_mbps
- plex.library.movies_count
- plex.library.shows_count
- plex.library.music_albums_count
- plex.sessions.rows
- plex.recently_added.count
- plex.recently_added.rows
- plex.summary

Recently-added formatting rules:
- Movies/music keep their original media type and title.
- TV entries (`episode`/`season`/`show`) are normalized to media type `show`.
- TV episode titles are rendered as `Show SnnEyy` when season/episode indexes exist.
- TV season titles are rendered as `Show Snn` when season index exists.
- `plex.recently_added.rows` is sorted newest-first using `addedAt`.
- Season rows are resolved to their most recently added episode when available.
- Row format for widgets is `LIBRARY|TITLE`.

Bandwidth rule:
- `plex.bandwidth.current_mbps` excludes paused sessions so a fully paused
    playback state reports `0.0` Mb/s.

Expanded per-item emits:
- plex.session_1.user
- plex.session_1.title
- plex.session_1.media_type
- plex.session_1.progress_percent
- plex.session_1.transcode_decision
- ... up to `CASEDD_PLEX_MAX_SESSIONS`
- plex.recently_added_1.title
- plex.recently_added_1.media_type
- plex.recently_added_1.library
- plex.recently_added_1.added_at
- ... up to `CASEDD_PLEX_MAX_RECENT`

Privacy settings:
- `CASEDD_PLEX_PRIVACY_FILTER_REGEX` optionally redacts matching user/title/library values.
- `CASEDD_PLEX_PRIVACY_FILTER_LIBRARIES` redacts exact library names (comma-separated,
  case-insensitive).
- `CASEDD_PLEX_PRIVACY_REDACTION_TEXT` controls replacement text (default `[hidden]`).
- Invalid regex values are ignored with a warning (getter continues running).

## InvokeAI API getter

Module: casedd/getters/invokeai.py

Configuration:
- CASEDD_INVOKEAI_BASE_URL (default: http://localhost:9090)
- CASEDD_INVOKEAI_API_TOKEN (optional bearer token)
- CASEDD_INVOKEAI_INTERVAL (default: 5)
- CASEDD_INVOKEAI_TIMEOUT (default: 4)
- CASEDD_INVOKEAI_VERIFY_TLS (default: 1)

Endpoints polled:
- GET /api/v1/queue/default/status (required)
- GET /api/v1/queue/default/current (optional enrichment)
- GET /api/v2/models/stats (optional enrichment, preferred)
- GET /api/v1/system/stats (optional enrichment fallback)
- GET /api/v1/images/names (optional latest-output discovery)
- GET /api/v1/images/i/{image_name}/metadata (optional latest-output enrichment)
- GET /api/v1/images/i/{image_name}/urls (optional latest-output preview URLs)
- GET /openapi.json (optional version fallback)

Emits:
- invokeai.version
- invokeai.queue.pending_count
- invokeai.queue.in_progress_count
- invokeai.queue.failed_count
- invokeai.last_job.id
- invokeai.last_job.status
- invokeai.last_job.model
- invokeai.last_job.dimensions
- invokeai.last_job.width
- invokeai.last_job.height
- invokeai.last_job.completed_at
- invokeai.system.vram_used_mb
- invokeai.system.vram_total_mb
- invokeai.system.vram_percent
- invokeai.models.cache_used_mb
- invokeai.models.cache_capacity_mb
- invokeai.models.cache_percent
- invokeai.models.loaded_count
- invokeai.latest_image.name
- invokeai.latest_image.thumbnail_url
- invokeai.latest_image.full_url

Version notes:
- Supported and validated against current InvokeAI Community Edition queue-scoped routes.
- The getter intentionally avoids `/api/v1/queue/default/list_all` and `/api/v1/app/version`
    as primary data sources because they are slow or timeout-prone on live hosts.
- Version is resolved from latest image metadata when available, then `/openapi.json`.
- Model cache fields are sourced from `models/stats`; template labels should treat them as
    cache usage, not guaranteed device VRAM telemetry.
- Optional endpoints are best-effort so minor API differences degrade gracefully.

Intentionally omitted in MVP:
- Thumbnail/image transport and gallery browsing payloads.
- Full graph/invocation payload expansion beyond top-level queue and last-job metadata.
- High-frequency per-step progress streams (dashboard polling remains lightweight).

## APOD getter (NASA Astronomy Picture of the Day)

Module: casedd/getters/apod.py

Config:
- `CASEDD_NASA_API_KEY` — NASA API key (default: public `DEMO_KEY`, limited to 30 req/hour/IP)
- `CASEDD_APOD_INTERVAL` — poll interval in seconds (default: `3600.0`)
- `CASEDD_APOD_CACHE_DIR` — local directory for cached images (default: `/tmp/casedd-apod`)

Emits:
- apod.available
- apod.date
- apod.title
- apod.copyright
- apod.explanation
- apod.media_type
- apod.image_path

Notes:
- Image changes at most once per day; the getter skips downloads when the cached date
  matches today.
- `apod.media_type` is `"image"` or `"video"`. Video APODs do not produce a local image.
- Use the `image` widget type with `source: apod.image_path` to display the image.

## htop getter

Module: casedd/getters/htop.py

Config:
- `CASEDD_HTOP_INTERVAL` — poll interval in seconds (default: `2.0`)
- `CASEDD_HTOP_MAX_ROWS` — maximum process rows emitted (default: `12`)
- `htop_interval` and `htop_max_rows` in `casedd.yaml`

Emits:
- htop.process_count
- htop.top_name
- htop.top_cpu
- htop.summary
- htop.rows

Notes:
- `htop.rows` is newline-delimited. Each row: `PID|CPU%|MEM%|Name`.
- Sorted by CPU utilization descending.
- Uses psutil exclusively; no external CLI required.

## Net ports getter

Module: casedd/getters/net_ports.py

Config:
- `CASEDD_NET_PORTS_INTERVAL` — poll interval in seconds (default: `5.0`)
- `net_ports_interval` in `casedd.yaml`

Emits:
- netports.port_count
- netports.rows

Notes:
- `netports.rows` is newline-delimited pipe-separated rows: `PROTO|PORT|ADDR|PID|NAME`.
- TCP: `LISTEN` state only; UDP: all bound sockets.
- Requires process access to resolve PIDs to names.
- Run as root (or with appropriate capabilities) for complete port visibility.

## NZBGet getter

Module: casedd/getters/nzbget.py

Config:
- `CASEDD_NZBGET_URL` — NZBGet JSON-RPC base URL (default: `http://localhost:6789`)
- `CASEDD_NZBGET_USERNAME` — optional username for HTTP basic auth
- `CASEDD_NZBGET_PASSWORD` — optional password for HTTP basic auth
- `CASEDD_NZBGET_INTERVAL` — poll interval in seconds (default: `5.0`)
- `CASEDD_NZBGET_TIMEOUT` — HTTP timeout in seconds (default: `3.0`)
- `CASEDD_NZBGET_CATEGORY_FILTER_REGEX` — optional regex to hide matching category names

Emits:
- nzbget.version
- nzbget.status.download_queue_enabled
- nzbget.status.download_paused
- nzbget.status.postprocess_paused
- nzbget.status.scan_paused
- nzbget.queue.total
- nzbget.queue.active_count
- nzbget.queue.current_count
- nzbget.queue.active_download_percent
- nzbget.queue.remaining_mb
- nzbget.queue.remaining_size
- nzbget.rate.mbps
- nzbget.eta_seconds
- nzbget.eta_hms
- nzbget.postprocess.active_count
- nzbget.history.success_count
- nzbget.history.failed_count

Per active job (numbered from 1):
- nzbget.current_1.name, nzbget.current_1.progress_percent, nzbget.current_1.category

Notes:
- `nzbget.queue.remaining_size` is a human-readable string (e.g. `"14.2 GB"`).
- `nzbget.eta_hms` is formatted as `HH:MM:SS`.
- Category names matching `CASEDD_NZBGET_CATEGORY_FILTER_REGEX` are replaced with `[hidden]`.

## Pi-hole getter

Module: casedd/getters/pihole.py

Config:
- `CASEDD_PIHOLE_BASE_URL` — Pi-hole base URL (default: `http://pi.hole`)
- `CASEDD_PIHOLE_PASSWORD` — Pi-hole app password for bearer auth (Pi-hole v6+)
- `CASEDD_PIHOLE_API_TOKEN` — legacy API token for bearer auth (Pi-hole v5)
- `CASEDD_PIHOLE_SESSION_SID` — optional pre-authenticated session SID
- `CASEDD_PIHOLE_INTERVAL` — poll interval in seconds (default: `5.0`)
- `CASEDD_PIHOLE_TIMEOUT` — HTTP timeout in seconds (default: `4.0`)
- `CASEDD_PIHOLE_VERIFY_TLS` — `0` to skip TLS verification (default: `1`)

Emits:
- pihole.version
- pihole.blocking.enabled
- pihole.queries.total
- pihole.queries.blocked
- pihole.queries.blocked_percent
- pihole.clients.active_count
- pihole.domains.blocked_count
- pihole.top_blocked.domain
- pihole.top_blocked.hits
- pihole.top_blocked.list
- pihole.top_client.name
- pihole.top_client.queries
- pihole.top_client.list

Notes:
- Supports Pi-hole v5 (API token) and v6 (app password) authentication flows.
- `*list` keys are newline-delimited `name|count` rows for table widgets.
- No auth required if the Pi-hole admin panel is open to the LAN.

## Sysinfo getter

Module: casedd/getters/sysinfo.py

Config:
- `CASEDD_SYSINFO_INTERVAL` — poll interval in seconds (default: `30.0`)
- `sysinfo_interval` in `casedd.yaml`

Emits:
- sysinfo.hostname
- sysinfo.os
- sysinfo.kernel
- sysinfo.uptime
- sysinfo.cpu_model
- sysinfo.cpu_cores
- sysinfo.memory
- sysinfo.disk_root
- sysinfo.ip
- sysinfo.rows

Notes:
- `sysinfo.rows` is newline-delimited `Label|Value` for table/panel widgets.
- `sysinfo.cpu_cores` format: `"4c / 8t"` (physical / logical).
- `sysinfo.memory` and `sysinfo.disk_root` are human-readable strings (e.g. `"5.2G / 32.0G"`).

## TrueNAS getter

Module: casedd/getters/truenas.py

Config:
- `CASEDD_TRUENAS_HOST` — TrueNAS hostname or IP (required)
- `CASEDD_TRUENAS_PORT` — HTTP(S) port (default: `80`)
- `CASEDD_TRUENAS_API_KEY` — TrueNAS API key (required)
- `CASEDD_TRUENAS_INTERVAL` — poll interval in seconds (default: `10.0`)
- `CASEDD_TRUENAS_TIMEOUT` — HTTP timeout in seconds (default: `5.0`)
- `CASEDD_TRUENAS_VERIFY_SSL` — `false` to skip TLS certificate verification (default: `true`)
- `CASEDD_TRUENAS_STRIP_DOMAIN_HOSTNAME` — strip domain suffix from hostname (default: `1`)

Emits:
- truenas.auth.ok
- truenas.system.reachable
- truenas.system.hostname
- truenas.system.model
- truenas.system.version
- truenas.system.uptime
- truenas.system.update_available
- truenas.system.update_status
- truenas.performance.cpu_temp_c
- truenas.users.count
- truenas.disks.rows
- truenas.pools.rows
- truenas.services.rows
- truenas.vms.count_total, truenas.vms.count_running, truenas.vms.count_stopped
- truenas.vms.rows
- truenas.jails.count_total, truenas.jails.count_running, truenas.jails.count_stopped
- truenas.jails.rows

Per-pool and per-disk keys (up to configured max):
- truenas.pool_\<n\>.name, truenas.pool_\<n\>.status, truenas.pool_\<n\>.used_percent, truenas.pool_\<n\>.free_tb, truenas.pool_\<n\>.total_tb
- truenas.disk_\<n\>.name, truenas.disk_\<n\>.status, truenas.disk_\<n\>.size_tb, truenas.disk_\<n\>.temp_c

Notes:
- Jails and VMs are optional: the getter skips these endpoints gracefully if TrueNAS
  does not expose them (e.g. TrueNAS SCALE vs CORE).
- An API key with at least read-only access to the system, pool, and disk APIs is sufficient.

## VMs getter (KVM / libvirt)

Module: casedd/getters/vms.py

Config:
- `CASEDD_VMS_PASSIVE` — `1` to disable virsh polling and accept push-only data (default: `0`)
- `CASEDD_VMS_COMMAND` — path to `virsh` binary (default: `virsh` on PATH)
- `CASEDD_VMS_INTERVAL` — poll interval in seconds (default: `10.0`)
- `CASEDD_VMS_MAX_ITEMS` — max per-VM detail entries emitted (default: `8`)

Emits:
- vms.available
- vms.mode
- vms.count_total
- vms.count_running
- vms.count_paused
- vms.count_shutoff
- vms.total_allocated_mib
- vms.total_actual_mib
- vms.total_cpu_percent
- vms.rows

Per-VM keys (up to `CASEDD_VMS_MAX_ITEMS`):
- vms.\<index\>.\* — per-VM fields for top N VMs

Notes:
- `vms.mode` is `"active"`, `"passive"`, or `"unavailable"`.
- `vms.rows` is newline-delimited `name|summary` rows for table display.
- Set `CASEDD_VMS_PASSIVE=1` together with push-mode updates to drive the widget without
  requiring libvirt tools on the host.

## Weather getter

Module: casedd/getters/weather.py

Config:
- `CASEDD_WEATHER_PROVIDER` — `nws` (US National Weather Service) or `open-meteo` (default: `nws`)
- `CASEDD_WEATHER_INTERVAL` — poll interval in seconds (default: `300.0`)
- `CASEDD_WEATHER_ZIPCODE` — US zipcode for automatic lat/lon lookup (NWS only)
- `CASEDD_WEATHER_LAT` — explicit latitude
- `CASEDD_WEATHER_LON` — explicit longitude
- `CASEDD_WEATHER_USER_AGENT` — HTTP User-Agent string required by NWS (default: `CASEDD/0.2`)

Emits:
- weather.provider
- weather.location
- weather.conditions
- weather.temp_f
- weather.humidity_percent
- weather.wind_mph
- weather.forecast_short
- weather.forecast_table
- weather.icon_url
- weather.alert_count
- weather.alert_active
- weather.alert_level
- weather.alert_summary
- weather.watch_warning
- weather.radar_url
- weather.radar_station
- weather.radar_image_url
- weather.radar_status
- weather.radar_error

Notes:
- Both providers emit the same `weather.*` key namespace.
- NWS requires a valid `User-Agent` header identifying your app and contact address.
- `open-meteo` does not require an API key and supports global coordinates.
- `weather.forecast_table` is newline-delimited for multi-row forecast widgets.
- `weather.radar_image_url` provides an animated radar image URL when available.

## Synology getter

Module: casedd/getters/synology.py

Config:
- `CASEDD_SYNOLOGY_HOST` — Synology DSM base URL (e.g. `http://nas1:5000`, required)
- `CASEDD_SYNOLOGY_USERNAME` — DSM username for API auth (required)
- `CASEDD_SYNOLOGY_PASSWORD` — DSM password for API auth (required)
- `CASEDD_SYNOLOGY_SID` — optional pre-authenticated session SID (skips login)
- `CASEDD_SYNOLOGY_INTERVAL` — poll interval in seconds (default: `20.0`)
- `CASEDD_SYNOLOGY_TIMEOUT` — HTTP timeout in seconds (default: `5.0`)
- `CASEDD_SYNOLOGY_VERIFY_TLS` — `0` to skip TLS certificate verification (default: `1`)
- `CASEDD_SYNOLOGY_STRIP_DOMAIN_HOSTNAME` — strip domain from displayed hostname (default: `1`)

Emits:
- synology.auth.ok
- synology.system.reachable
- synology.system.hostname
- synology.system.model
- synology.system.version
- synology.dsm.update_available
- synology.dsm.latest_version
- synology.performance.cpu_percent
- synology.performance.cpu_temp_c
- synology.performance.ram_percent
- synology.performance.disk_read_mb_s
- synology.performance.disk_write_mb_s
- synology.performance.net_rx_kbps
- synology.performance.net_tx_kbps
- synology.storage.critical_count
- synology.storage.warning_count
- synology.storagepool.count
- synology.volume.count
- synology.shares.count
- synology.users.count
- synology.backup.installed
- synology.backup.configured
- synology.backup.success
- synology.backup.summary
- synology.surveillance.available
- synology.surveillance.camera_count
- synology.surveillance.recording_count
- synology.disks.rows
- synology.shares.rows
- synology.users.rows
- synology.services.rows
- synology.status.rows
- synology.backup.rows
- synology.cameras.rows
- synology.surveillance.status.rows

Notes:
- Requires a DSM account with at least read access; admin-equivalent is recommended for full
  telemetry (disk health, services, backup status).
- Surveillance Station keys are only populated when Surveillance Station is installed.
- `synology.services.rows` includes state for common packages
  (File Station, SMB, Synology Drive, Hyper Backup, Surveillance Station, Active Backup).

## Template-aware polling

CASEDD runs getters required by templates that can become active under policy
(current/rotated/scheduled/triggered templates across panels).

You can force specific namespaces to always collect via
`CASEDD_ALWAYS_COLLECT_PREFIXES` (for example `cpu,memory,system`).

When `casedd.test_mode` is enabled (or `CASEDD_TEST_MODE=1` on startup),
all getters are disabled globally and only pushed/simulated values are used.
