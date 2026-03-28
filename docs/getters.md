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
- disk.read_mbps
- disk.write_mbps

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

Status defaults:
- good: >= 90% of advertised
- marginal: < 90%
- critical: < 70%

## Ollama API getter

Module: casedd/getters/ollama.py

API endpoint used:
- GET {CASEDD_OLLAMA_API_BASE}/api/ps

Emits:
- ollama.active_count
- ollama.active_models
- ollama.primary_model
- ollama.primary_size_gb
- ollama.primary_gpu_percent
- ollama.primary_cpu_percent
- ollama.primary_ttl
- ollama.summary

Notes:
- This getter uses the HTTP API only and does not require the ollama command.
- CPU/GPU percentages are parsed from optional processor text when present in API payload.

## Template-aware polling

CASEDD runs getters required by templates that can become active under policy
(current/rotated/scheduled/triggered templates across panels).

You can force specific namespaces to always collect via
`CASEDD_ALWAYS_COLLECT_PREFIXES` (for example `cpu,memory,system`).

When `casedd.test_mode` is enabled (or `CASEDD_TEST_MODE=1` on startup),
all getters are disabled globally and only pushed/simulated values are used.
