# CASEDD — Case Display Daemon

A lightweight, high-performance Python daemon that drives a small USB framebuffer display
mounted inside a PC case, while simultaneously serving the same content over WebSocket and
HTTP for remote viewing.

**Target hardware:** Waveshare 5-inch USB Monitor, 800×480, Type-C  
**OS:** Ubuntu 24.04 (headless)  
**Stack:** Python 3.12, FastAPI, uvicorn, Pillow, Pydantic v2, psutil, PyYAML

---

## Features

- **Dual output** — push rendered images to `/dev/fb1` (framebuffer) AND a browser via WebSocket simultaneously
- **Custom layout engine** — declare layouts in `.casedd` YAML files using CSS Grid Template Areas syntax; widget tree supports unlimited nesting via `type: panel`
- **10 widget types** — `value`, `text`, `bar`, `gauge`, `histogram`, `sparkline`, `image`, `slideshow`, `clock`, `panel`
- **Live data getters** — CPU temp/percent/fan, GPU (nvidia-smi, graceful fallback), RAM, disk, network rates, hostname, uptime
- **External data push** — accept JSON updates via Unix domain socket or REST POST; values cached in RAM and used on next render
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
./dev.sh docs       # generate API docs to docs/api.json (local only)
```

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
echo '{"update": {"outside_temp_c": 21.5}}' | nc -U /run/casedd/casedd.sock
```

Or via REST:

```bash
curl -X POST http://localhost:8080/update \
  -H "Content-Type: application/json" \
  -d '{"update": {"outside_temp_c": 21.5}}'
```

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

---

## API

Interactive API docs are available at `http://localhost:8080/docs` when the daemon is running.

To generate a static `docs/api.json`:

```bash
./dev.sh docs
```

---

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
