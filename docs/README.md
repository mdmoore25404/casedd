---
title: Project Overview
nav_order: 1
permalink: /overview/
---



A lightweight, high-performance Python daemon that drives a small USB framebuffer display
mounted inside a PC case, while simultaneously serving the same content over WebSocket and
HTTP for remote viewing.

**Target hardware:** Waveshare 5-inch USB Monitor, 800×480, Type-C  
**OS:** Ubuntu 24.04 (headless)  
**Stack:** Python 3.12, FastAPI, uvicorn, Pillow, Pydantic v2, psutil, PyYAML

---
## Licensing

**casedd** is released under the **[Business Source License 1.1](LICENSE)**.

- **Free** for personal, hobbyist, and home-lab use (including AI workstations and single-user setups).
- **Commercial use**, white-labeling, enterprise deployments, or bundling with hardware requires a paid commercial license.

See [`LICENSE`](LICENSE) and [`license-commercial.md`](license-commercial.md) for full details.

Interested in commercial use or white-label rights? Feel free to reach out.


## Features

- **Dual output** — push rendered images to `/dev/fb1` (framebuffer) AND a browser via WebSocket simultaneously
- **Custom layout engine** — declare layouts in `.casedd` YAML files using CSS Grid Template Areas syntax; widget tree supports unlimited nesting via `type: panel`
- **10 widget types** — `value`, `text`, `bar`, `gauge`, `histogram`, `sparkline`, `image`, `slideshow`, `clock`, `panel`
- **Live data getters** — CPU, fan telemetry (CPU/system/GPU), NVIDIA GPU (including multi-GPU keys), RAM, disk, network, system uptime/host, speedtest, Ollama API runtime state
- **Template policy engine** — rotate templates, schedule templates by time/day, and trigger template overrides from data-store conditions
- **Speedtest integration** — optional Ookla CLI getter (default every 30 min) with plan-relative metrics and status keys
- **External data push** — accept JSON updates via Unix domain socket or REST POST; values cached in RAM and used on next render
- **Write-endpoint auth and throttling** — protect update endpoints with `X-API-Key`, HTTP Basic Auth, and optional per-IP rate limiting
- **Template-aware polling** — getters run only when their key namespaces are referenced by the active template
- **Operational health** — health and metrics endpoints expose daemon and per-getter state
- **CLI control surface** — `casedd-ctl` provides status, health, template, metrics, snapshot, data, and reload commands
- **Dev-friendly** — `CASEDD_NO_FB=1` disables framebuffer for dev; browser WebSocket view is the primary dev display
- **Multiple deployment modes** — plain Python, systemd service, Docker Compose

---

## Quick start

### 1. Clone and create the venv

```bash
git clone https://github.com/mdmoore25404/casedd.git
cd casedd
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set CASEDD_NO_FB=1 if no framebuffer hardware
```

### 3. Run (dev mode)

```bash
./dev.sh start
# Open http://localhost:8080 in your browser to see the live display
./dev.sh logs      # tail the log
./dev.sh status    # check daemon health
./dev.sh stop
```

---

## Development workflow

```bash
./dev.sh start      # start daemon in background (venv + .env loaded automatically)
./dev.sh stop       # stop daemon cleanly
./dev.sh restart    # stop + start
./dev.sh status     # check PID + last log lines
./dev.sh logs       # tail -f the log file
./dev.sh lint       # ruff check + mypy --strict (must be zero errors)
./dev.sh test       # pytest with coverage
./dev.sh test --fast # pytest without coverage
./dev.sh docs       # generate API docs to docs/api.json (local only)
```

### CLI (`casedd-ctl`)

```bash
./casedd-ctl status
./casedd-ctl health
./casedd-ctl templates list
./casedd-ctl help templates
./casedd-ctl help templates set
./casedd-ctl --json health
```

See [docs/cli.md](cli.md) for the full command reference.

### Linting (must be clean before any commit)

```bash
source .venv/bin/activate
ruff check .
mypy --strict casedd/
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        daemon.py                        │
│  async event loop — orchestrates all subsystems         │
└────┬──────────┬──────────┬───────────────┬──────────────┘
     │          │           │               │
 getters/   template/   renderer/       outputs/
 (pollers)  (layout)    (PIL image)     ├─ framebuffer.py
                                        ├─ websocket.py
                                        └─ http_viewer.py
                                    ingestion/
                                    ├─ unix_socket.py
                                    └─ rest.py
```

### Data flow

1. **Getters** poll system APIs at their own interval and push values into the **data store** (in-RAM key/value, dotted keys: `cpu.temperature`, `memory.percent`, etc.)
2. External processes can push values the same way via **Unix socket** (`/run/casedd/casedd.sock`) or **REST POST** (`POST /update`)
3. Every render cycle, the **renderer** reads the active **template** (a `.casedd` YAML file) and the data store, and produces a `PIL.Image`
4. The image is simultaneously pushed to **framebuffer** (if enabled) and all connected **WebSocket clients**

---

## Template format (.casedd)

Templates are YAML files in `templates/`. See [docs/template_format.md](docs/template_format.md) for the full specification.
Getter key reference lives at [docs/getters.md](docs/getters.md).

- API docs are live from [docs/api.json](docs/api.json).
- Template examples are available in [templates/](templates/) and in [docs/index.md](docs/index.md) for GitHub Pages.

Quick example:

```yaml
name: simple_stats
aspect_ratio: "5:3"
layout_mode: fit
background: "#1a1a2e"
refresh_rate: 2.0

grid:
  template_areas: |
    "cpu  gpu  ram"
    "disk disk net"
  columns: "1fr 1fr 1fr"
  rows: "1fr 1fr"

widgets:
  cpu:
    type: gauge
    source: cpu.percent
    label: "CPU"
  gpu:
    type: gauge
    source: nvidia.percent
    label: "GPU"
  ram:
    type: bar
    source: memory.percent
    label: "RAM"
  disk:     # spans 2 columns because "disk disk" in template_areas
    type: bar
    source: disk.percent
    label: "Disk"
  net:
    type: panel
    direction: column
    children:
      - type: sparkline
        source: net.bytes_recv_rate
        label: "↓"
      - type: sparkline
        source: net.bytes_sent_rate
        label: "↑"
```

---

## Data push via Unix socket

```bash
echo '{"update": {"outside_temp_f": 72.0}}' | nc -U /run/casedd/casedd.sock
```

Or via REST:

```bash
curl -X POST http://localhost:8080/update \
  -H "Content-Type: application/json" \
  -d '{"update": {"outside_temp_f": 72.0}}'
```

### Browser push tester (dev)

The web viewer now includes a built-in push test panel:

1. Open `http://localhost:8080`
2. Click the status badge (`live` / `reconnecting`) to expand details
3. Use **push test update** to send key/value updates to `POST /update`

This is useful for quickly testing ingestion without leaving the browser.

### Push demo template

An example template is provided at [templates/push_demo.casedd](templates/push_demo.casedd).
It visualizes externally pushed values like `outside_temp_f` and `custom.note`.

To try it:

```bash
# 1) Set the active template
export CASEDD_TEMPLATE=push_demo
./dev.sh restart

# 2) Push demo values
./scripts/push_demo.sh 72.0 "Patio sensor online"
```

### Fan telemetry template

An example fan dashboard is provided at [templates/fans.casedd](templates/fans.casedd).

To try it:

```bash
export CASEDD_TEMPLATE=fans
./dev.sh restart
```

This template visualizes:
- CPU fan count / avg / max
- system fan count / avg / max
- GPU fan count / avg / max (percent when sourced from nvidia-smi)

### Immediate speedtest push helper

Run an on-demand Ookla speedtest and push the result into CASEDD via REST:

```bash
./scripts/speedtest_push.sh
```

This writes ``speedtest.*`` keys including down/up Mb/s, ping, jitter,
plan-relative percentages, status, and summary text.

---

## Deployment

### systemd

```bash
sudo ./deploy/install/install.sh
sudo systemctl status casedd
sudo ./deploy/install/uninstall.sh
```

Notes:
- The installer runs CASEDD directly from the current clone path instead of copying to `/opt`.
- It preserves an existing `/etc/casedd/casedd.env` file and only installs the template once.
- If the repo moves, rerun `sudo ./deploy/install/install.sh` from the new location.
- Uninstall preserves config, logs, and `.venv` unless you pass purge flags.

### Docker Compose

```bash
cp .env.example .env  # configure as needed
docker compose up -d
docker compose logs -f
```

---

## API

Interactive API docs are available at `http://localhost:8080/docs` when the daemon is running.

To generate a static `docs/api.json`:

```bash
./dev.sh docs
```

## Timezone

The clock widget renders the host machine's local time. If the host timezone is
wrong, set it at the OS level (example for US Eastern):

```bash
sudo timedatectl set-timezone America/New_York
timedatectl | grep "Time zone"
```

## Speedtest configuration

Speedtest polling and threshold behavior can be tuned via environment variables:

```bash
CASEDD_SPEEDTEST_INTERVAL=1800
CASEDD_SPEEDTEST_STARTUP_DELAY=0
CASEDD_SPEEDTEST_BINARY=speedtest
CASEDD_SPEEDTEST_ADVERTISED_DOWN_MBPS=2000
CASEDD_SPEEDTEST_ADVERTISED_UP_MBPS=200
CASEDD_SPEEDTEST_MARGINAL_RATIO=0.9
CASEDD_SPEEDTEST_CRITICAL_RATIO=0.7
CASEDD_OLLAMA_API_BASE=http://localhost:11434
CASEDD_OLLAMA_INTERVAL=10
CASEDD_OLLAMA_TIMEOUT=3
```

Notes:
- `CASEDD_SPEEDTEST_STARTUP_DELAY` delays the first speedtest after startup.
  Set this to `60`-`300` in production to avoid startup-time network/CPU spikes.

## Framebuffer performance and debug flags

```bash
CASEDD_FB_DEVICE=/dev/fb0
CASEDD_FB_ROTATION=0
CASEDD_FB_CLAIM_ON_NO_INPUT=1
CASEDD_DEBUG_FRAME_LOGS=0
CASEDD_LOG_LEVEL=INFO
CASEDD_STARTUP_FRAME_SECONDS=5
```

Notes:
- Keep `CASEDD_DEBUG_FRAME_LOGS=0` for production; enable only while debugging.
- `CASEDD_FB_CLAIM_ON_NO_INPUT=1` enables inputless display takeover behavior.
- `CASEDD_FB_ROTATION` supports `0`, `90`, `180`, `270`.
- `CASEDD_STARTUP_FRAME_SECONDS` keeps a startup status frame on screen while getters warm up before live data rendering begins.

## Dev vs production

- The production systemd service in `deploy/casedd.service` forces:
  - `CASEDD_LOG_LEVEL=NONE`
  - `CASEDD_DEBUG_FRAME_LOGS=0`
  - framebuffer output enabled
- The install script renders the production unit from `deploy/casedd.service` using the live clone
  path, so the checked-in unit file acts as a template.
- `./dev.sh` forces a development profile:
  - `CASEDD_DEV_LOG_LEVEL=DEBUG` by default
  - `CASEDD_DEV_DEBUG_FRAME_LOGS=1` by default
  - `CASEDD_DEV_NO_FB=1` by default so iteration happens in the web UI, not on the real framebuffer

You can override dev behavior with:

```bash
CASEDD_DEV_LOG_LEVEL=INFO
CASEDD_DEV_DEBUG_FRAME_LOGS=0
CASEDD_DEV_NO_FB=0
```

## Template rotation, schedule, and triggers

Rotation can be configured from environment variables:

```bash
CASEDD_TEMPLATE=system_stats
CASEDD_TEMPLATE_ROTATION=fans,slideshow
CASEDD_TEMPLATE_ROTATION_INTERVAL=30
```

Schedules and triggers are configured in `casedd.yaml`.
See [casedd.yaml.example](casedd.yaml.example) for a complete sample:

```yaml
template_schedule:
  - template: slideshow
    start: "23:00"
    end: "06:00"
    days: [0, 1, 2, 3, 4, 5, 6]

template_triggers:
  - source: cpu.percent
    operator: gte
    value: 90
    template: system_stats
    duration: 10
    hold_for: 20
    clear_operator: lte
    clear_value: 70
    cooldown: 30
    priority: 10
```

Selection priority is:
1. Trigger rules
2. Schedule rules
3. Rotation list
4. Base `CASEDD_TEMPLATE`

---

## Licensing

casedd is released under the **Business Source License 1.1**.

- **Free** for personal, hobbyist, and home-lab use.
- **Commercial use**, white-labeling, or enterprise deployments require a paid license.

See [`LICENSE`](LICENSE) and [`LICENSE-COMMERCIAL.md`](LICENSE-COMMERCIAL.md) for full details.

## Directory structure

```
casedd/          Python package (daemon source code)
templates/       .casedd layout/widget definition files
assets/          Static assets (images, fonts)
  slideshow/     Images cycled by the slideshow widget
deploy/          systemd unit + install script
docs/            API JSON + template format spec
scripts/         Local dev scripts (not CI)
run/             PID files (dev, git-ignored)
logs/            Log files (dev, git-ignored)
```

---

## Contributing

1. Pick an issue (or create one)
2. Create a branch: `git checkout -b issue/<number>-<slug>`
3. Write code — `ruff check .` and `mypy --strict casedd/` must pass
4. Open a PR to `main`

All commit messages follow `<type>(<scope>): <summary>` (e.g. `feat(renderer): add gauge widget`).
