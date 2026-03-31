---
title: REST API
nav_order: 4
permalink: /api/
---

# REST API Reference

CASEDD exposes a full REST API via [FastAPI](https://fastapi.tiangolo.com/).
When a daemon is running locally, interactive Swagger UI docs are available at:

```
http://localhost:8080/docs
```

The OpenAPI descriptor (JSON) is available at:

- **Live daemon:** `http://localhost:8080/openapi.json`
- **Static snapshot:** [`docs/api.json`](https://github.com/mdmoore25404/casedd/blob/main/docs/api.json)

---

## Core endpoints

### `GET /image`

Returns the most recently rendered frame as a JPEG image.

| Parameter | Type   | Default | Description              |
|-----------|--------|---------|--------------------------|
| `panel`   | string | primary | Panel name to view       |

```bash
curl http://localhost:8080/image --output frame.jpg
```

---

### `GET /api/panels`

Returns all configured panels and their current state.

```json
{
  "default_panel": "primary",
  "test_mode": false,
  "panels": [
    {
      "name": "primary",
      "display_name": "Primary",
      "width": 800,
      "height": 480,
      "base_template": "system_stats",
      "rotation_templates": [],
      "rotation_interval": 30.0,
      "rotation_enabled": true,
      "current_template": "system_stats",
      "forced_template": ""
    }
  ]
}
```

---

### `GET /api/panels/{name}/rotation`

Returns the live rotation configuration for a panel.

```bash
curl http://localhost:8080/api/panels/primary/rotation
```

```json
{
  "base_template": "system_stats",
  "rotation_templates": ["htop", "slideshow"],
  "rotation_interval": 30.0,
  "rotation_enabled": true
}
```

---

### `PUT /api/panels/{name}/rotation`

Updates the rotation configuration for a panel at runtime.

```bash
curl -X PUT http://localhost:8080/api/panels/primary/rotation \
  -H "Content-Type: application/json" \
  -d '{"rotation_templates": ["htop", "slideshow"], "rotation_interval": 30, "rotation_enabled": true}'
```

---

### `POST /api/update`

Pushes data values into the store. Same format as the Unix socket receiver.

Authentication options:

- `X-API-Key: <secret>` when `CASEDD_API_KEY` is configured
- HTTP Basic Auth when `CASEDD_API_BASIC_USER` and `CASEDD_API_BASIC_PASSWORD` are configured

Rate limiting:

- When `CASEDD_API_RATE_LIMIT` is greater than `0`, excess requests return `429`

```bash
curl -X POST http://localhost:8080/api/update \
  -H "Content-Type: application/json" \
  -d '{"update": {"outside_temp_f": 72.0, "custom.note": "hello"}}'
```

With API key:

```bash
curl -X POST http://localhost:8080/api/update \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-shared-secret" \
  -d '{"update": {"outside_temp_f": 72.0}}'
```

With HTTP Basic Auth:

```bash
curl -X POST http://localhost:8080/api/update \
  -H "Content-Type: application/json" \
  -u devuser:devpass \
  -d '{"update": {"outside_temp_f": 72.0}}'
```

The legacy endpoint `POST /update` enforces the same auth and rate-limit rules.

---

### `GET /api/health`

Returns daemon health, active panel template selection, uptime, render count,
and per-getter state.

Getter statuses include:

- `inactive` for getters that are registered but not currently scheduled
- `starting` for getters that are running but have not reported success yet
- `ok` for healthy getters
- `error` for getters currently failing

```bash
curl http://localhost:8080/api/health
```

---

### `GET /api/metrics`

Returns Prometheus-format metrics for daemon uptime, render count, getter
error counts, getter up/down state, and store key count.

```bash
curl http://localhost:8080/api/metrics
```

---

### `GET /api/templates`

Lists all available `.casedd` template files.

```json
{ "templates": ["system_stats", "htop", "slideshow", "apod"] }
```

---

### `GET /api/templates/{name}`

Loads and returns the parsed content of a template file.

---

### `PUT /api/templates/{name}`

Saves new YAML content to a template file. The daemon hot-reloads the change
on the next render cycle.

---

### `POST /api/panels/{name}/force-template`

Immediately overrides the active template for a panel, bypassing rotation and
schedule rules.

```bash
curl -X POST http://localhost:8080/api/panels/primary/force-template \
  -H "Content-Type: application/json" \
  -d '{"template": "htop"}'
```

Send `{"template": ""}` to clear the override and return to normal selection.

---

### `POST /api/test-mode`

Enable or disable test mode. In test mode, all live getters are paused and
only externally pushed data drives the display.

```bash
curl -X POST http://localhost:8080/api/test-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

---

### `POST /api/simulate`

Run a simulation scenario. Available modes:

| Mode     | Description                                              |
|----------|----------------------------------------------------------|
| `random` | Generate random values for all store keys in the template |
| `replay` | Replay a list of JSON update payloads at a set interval  |
| `stop`   | Stop the current simulation                              |

```bash
curl -X POST http://localhost:8080/api/simulate \
  -H "Content-Type: application/json" \
  -d '{"mode": "random", "interval": 1.0}'
```

---

## Unix socket ingestion

Any JSON update can also be pushed via the Unix domain socket at
`/run/casedd/casedd.sock` (configurable via `CASEDD_SOCKET_PATH`):

```bash
echo '{"update": {"cpu.percent": 95.0}}' | nc -U /run/casedd/casedd.sock
```

This is useful for custom getter scripts, external sensors, or any process
that doesn't want to make an HTTP call.
