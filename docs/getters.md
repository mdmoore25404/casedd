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

Emits:
- cpu.percent
- cpu.temperature
- cpu.fan_rpm

Notes:
- Temperature keys depend on host sensors (coretemp/k10temp/acpitz/cpu_thermal).
- cpu.fan_rpm remains for compatibility; richer fan telemetry is emitted by fan getter.

## Fan getter

Module: casedd/getters/fans.py

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

Emits backward-compatible primary keys (GPU 0 when present):
- nvidia.percent
- nvidia.temperature
- nvidia.memory_used_mb
- nvidia.memory_total_mb
- nvidia.power_w

Emits multi-GPU keys when multiple GPUs are present:
- nvidia.gpu_count
- nvidia.0.percent, nvidia.0.temperature, nvidia.0.memory_used_mb, nvidia.0.memory_total_mb, nvidia.0.power_w
- nvidia.1.percent, nvidia.1.temperature, nvidia.1.memory_used_mb, nvidia.1.memory_total_mb, nvidia.1.power_w
- etc. for each GPU index
- nvidia.total_memory_used_mb
- nvidia.total_memory_mb

nvidia-smi fields currently queried:
- index
- utilization.gpu
- temperature.gpu
- memory.used
- memory.total
- power.draw

Notes:
- If nvidia-smi is absent, the getter disables itself gracefully.

## Memory getter

Module: casedd/getters/memory.py

Emits:
- memory.percent
- memory.used_gb
- memory.total_gb
- memory.available_gb

## Disk getter

Module: casedd/getters/disk.py

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

Emits:
- net.bytes_recv_rate
- net.bytes_sent_rate
- net.recv_mbps
- net.sent_mbps
- net.bytes_recv_total
- net.bytes_sent_total

## System getter

Module: casedd/getters/system.py

Emits:
- system.hostname
- system.uptime
- system.load_1
- system.load_5
- system.load_15

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
- os_updates.rows
- os_updates.summary

Notes:
- Security classification is best-effort.
- apt uses channel hints like `*-security` from `apt list --upgradable`.
- apt phasing is detected from `apt -s upgrade` deferred/phasing output when available.
- dnf enriches security rows using `dnf updateinfo list security --updates` when available.
- `os_updates.rows` renders package rows in `name|version` format and may append
  `[SEC]` and `(phasing)` markers.

Status defaults:
- good: >= 90% of advertised
- marginal: < 90%
- critical: < 70%

## Ollama API getter

Module: casedd/getters/ollama.py

API endpoint used:
- ollama.primary_cpu_percent
- ollama.primary_ttl
- ollama.version
- ollama.models.local_count

## Servarr getters (Radarr / Sonarr)

Module: casedd/getters/servarr.py

Supported in this iteration:
- Radarr
- Sonarr

Per-app emits:
- radarr.active / sonarr.active
- radarr.queue.total / sonarr.queue.total
- radarr.queue.downloading / sonarr.queue.downloading
- radarr.queue.importing / sonarr.queue.importing
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
- ollama.running.rows
- ollama.running_1.name
- ollama.running_1.size_bytes
- ollama.running_1.size_vram_bytes
- ollama.running_1.expires_at
- ollama.running_1.ttl
- ollama.running_1.family
- ollama.running_1.parameter_size
- ollama.running_1.quantization_level
- ollama.model_1.name
- ollama.model_1.modified_at
- ollama.model_1.size_bytes
- ollama.model_1.family
- ollama.model_1.parameter_size
- ollama.model_1.quantization_level
- ... continued up to CASEDD_OLLAMA_DETAIL_MAX_MODELS

Notes:
- This getter uses the HTTP API only and does not require the ollama command.
- CPU/GPU percentages are parsed from optional processor text when present in API payload.
- Per-request live token/sec telemetry is intentionally not exposed because
    Ollama does not publish that as a pull-based metric endpoint.

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

## Template-aware polling

CASEDD runs getters required by templates that can become active under policy
(current/rotated/scheduled/triggered templates across panels).

You can force specific namespaces to always collect via
`CASEDD_ALWAYS_COLLECT_PREFIXES` (for example `cpu,memory,system`).

When `casedd.test_mode` is enabled (or `CASEDD_TEST_MODE=1` on startup),
all getters are disabled globally and only pushed/simulated values are used.
