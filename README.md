# CASEDD — Case Display Daemon

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
- **11 widget types** — `value`, `text`, `bar`, `gauge`, `histogram`, `sparkline`, `image`, `slideshow`, `clock`, `panel`, `ups`
- **Live data getters** — CPU, fan telemetry (CPU/system/GPU), NVIDIA GPU (including multi-GPU keys), RAM, disk, network, system uptime/host, speedtest, Ollama API runtime state, UPS telemetry
- **Template policy engine** — rotate templates, schedule templates by time/day, and trigger template overrides from data-store conditions
- **Speedtest integration** — optional Ookla CLI getter (default every 30 min) with plan-relative metrics and status keys
- **External data push** — accept JSON updates via Unix domain socket or REST POST; values cached in RAM and used on next render
- **Template-aware polling** — getters run only when their key namespaces are referenced by the active template
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
# Open http://localhost:8080 for the lightweight live viewer
# Open http://localhost:8080/app for the advanced app (Vite dev mode)
./dev.sh logs      # tail the log
./dev.sh status    # check daemon health
./dev.sh stop
```

---

## Development workflow

```bash
./dev.sh start      # start daemon + advanced app (Vite hot-reload) in background
./dev.sh stop       # stop daemon + advanced app cleanly
./dev.sh restart    # stop + start
./dev.sh status     # check daemon/app PIDs + last log lines
./dev.sh logs       # tail -f the log file
./dev.sh lint       # ruff check + mypy --strict (must be zero errors)
./dev.sh docs       # generate API docs to docs/api.json (local only)
```

### Advanced React app (Vite)

`./dev.sh start` already launches the advanced app in Vite development mode for
hot-reload editing. You can still run it manually when needed:

```bash
cd web
npm install
npm run dev
```

The advanced app is built with React + Vite + Bootstrap + FontAwesome.
It targets the CASEDD API for template overrides, test mode, and simulation.

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
width: 800
height: 480
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
curl -X POST http://localhost:8080/api/update \
  -H "Content-Type: application/json" \
  -d '{"update": {"outside_temp_f": 72.0}}'
```

The lightweight viewer intentionally stays minimal (live state + panel picker).
Use the advanced app for data push/testing/simulation workflows.

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

### Htop-style process template

A single-widget process table template is provided at [templates/htop.casedd](templates/htop.casedd).

```bash
export CASEDD_TEMPLATE=htop
./dev.sh restart
```

The ``htop`` widget shows top processes sorted by CPU utilization.

### Weather templates (NWS + external provider example)

Weather templates are provided at:
- [templates/weather_nws.casedd](templates/weather_nws.casedd)
- [templates/weather_external.casedd](templates/weather_external.casedd)

NWS mode (official US APIs):

```bash
export CASEDD_TEMPLATE=weather_nws
export CASEDD_WEATHER_PROVIDER=nws
export CASEDD_WEATHER_ZIPCODE=20852
./dev.sh restart
```

External provider example (Open-Meteo):

```bash
export CASEDD_TEMPLATE=weather_external
export CASEDD_WEATHER_PROVIDER=open-meteo
export CASEDD_WEATHER_LAT=38.9856
export CASEDD_WEATHER_LON=-77.0947
./dev.sh restart
```

Both providers emit the same ``weather.*`` keys so the same widgets/templates
can be reused without NWS-specific rendering logic.

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
sudo deploy/install/install.sh   # copies service file, enables + starts
sudo systemctl status casedd
```

### Docker Compose

```bash
cp .env.example .env  # configure as needed
docker compose up -d
docker compose logs -f
```

Compose starts two services:
- `casedd` backend (HTTP/WS)
- `casedd-web` advanced app (Vite dev mode with hot reload)

For host metric visibility, the `casedd` container bind-mounts host Linux
runtime filesystems read-only:
- `/proc` -> `/host/proc`
- `/sys` -> `/host/sys`
- `/run` -> `/host/run`

`CASEDD_PROCFS_PATH` is set to `/host/proc` in Compose so psutil-based getters
read host process/system views instead of container-local procfs.

Default Docker URLs:
- lightweight viewer: `http://localhost:18080/`
- advanced app redirect entry: `http://localhost:18080/app`
- direct advanced app: `http://localhost:15173/`

---

## API

Interactive API docs are available at `http://localhost:8080/docs` when the daemon is running.

Key runtime endpoints:

- `GET /api/panels` — panel metadata and current/forced template state
- `GET /image?panel=<name>` — latest PNG for a specific panel
- `POST /api/template/override` — force/clear per-panel template override
- `GET/POST /api/test-mode` — global getter-disable test mode
- `POST /api/sim/replay` — replay deterministic records
- `POST /api/sim/random` — start bounded random simulation
- `POST /api/sim/stop` / `GET /api/sim/status`
- `GET /api/debug/render-state` — in-memory sparkline/histogram buffers

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
CASEDD_SPEEDTEST_BINARY=speedtest
CASEDD_SPEEDTEST_ADVERTISED_DOWN_MBPS=2000
CASEDD_SPEEDTEST_ADVERTISED_UP_MBPS=200
CASEDD_SPEEDTEST_MARGINAL_RATIO=0.9
CASEDD_SPEEDTEST_CRITICAL_RATIO=0.7
CASEDD_OLLAMA_API_BASE=http://localhost:11434
CASEDD_OLLAMA_INTERVAL=10
CASEDD_OLLAMA_TIMEOUT=3
CASEDD_UPS_COMMAND=
CASEDD_UPS_INTERVAL=5
CASEDD_UPS_UPSC_TARGET=ups@localhost
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
