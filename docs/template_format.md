---
title: CASEDD Template Format
layout: default
---

# CASEDD Template Format Specification

Welcome to the CASEDD template docs. This page explains how to define screen layouts and widgets in `.casedd` YAML templates.

Version: 1.0  
File extension: `.casedd`  
Format: YAML

Templates live in the `templates/` directory. The active template is selected via
`CASEDD_TEMPLATE` (environment variable or `casedd.yaml` config key).

---

## Top-level keys

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | yes | — | Unique template name (must match filename without extension) |
| `description` | string | no | `""` | Human-readable description |
| `width` | int | no | 800 | Canvas width in pixels |
| `height` | int | no | 480 | Canvas height in pixels |
| `background` | string | no | `"#000000"` | Canvas background color (hex, rgb(), or gradient — see Color section) |
| `refresh_rate` | float | no | config default | Render frequency in Hz (frame rate) |
| `grid` | Grid | yes | — | Layout definition (see Grid section) |
| `widgets` | dict[str, Widget] | yes | — | Widget definitions keyed by name |

---

## Grid

The `grid` key defines how widgets are placed on the canvas, using the
[CSS Grid Template Areas](https://developer.mozilla.org/en-US/docs/Web/CSS/grid-template-areas)
syntax. No browser is required — the grid solver is a pure Python implementation.

```yaml
grid:
  template_areas: |
    "header header header"
    "cpu    gpu    ram"
    "disk   disk   net"
  columns: "1fr 1fr 1fr"
  rows: "80px 1fr 1fr"
```

### `template_areas`

A multi-line string where each quoted line represents one row of cells.
Each cell name must match a key in `widgets`.

**Spanning:** Repeat a widget name across cells to make it span those columns or rows.
A widget spanning cells must form a contiguous rectangle — the same rules as CSS Grid.

```
"disk disk net"   ← disk spans 2 columns
"disk disk net"   ← disk also spans this row → disk is a 2×2 block
```

### `columns` / `rows`

Space-separated track sizes. Supported units:

| Unit | Meaning |
|------|---------|
| `Xfr` | Fractional — divide remaining space proportionally |
| `Xpx` | Exact pixel size |
| `X%` | Percentage of canvas width (columns) or height (rows) |

Mixed units are allowed: `"200px 1fr 1fr"` gives the first column 200px and splits the rest equally.

---

## Widgets

Every widget is defined under the `widgets` key by a name that appears in `template_areas`.

```yaml
widgets:
  cpu:
    type: gauge
    source: cpu.percent
    label: "CPU"
    color_stops:
      - [0,  "#6bcb77"]
      - [70, "#ffd93d"]
      - [90, "#ff6b6b"]
```

### Common fields (all widget types)

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Widget type — see Widget Types |
| `source` | string | Dotted data store key (e.g. `cpu.temperature`). Value is read each render. |
| `content` | string | Literal static string. Used when no live data is needed. |
| `label` | string | Display label drawn near the widget |
| `color` | string | Primary color (hex or rgb()) |
| `background` | string | Widget background (defaults to transparent / parent bg) |
| `border_style` | string | Cell border style: `none`, `solid`, `dashed`, `dotted`, `inset`, `outset` |
| `border_color` | string | Border color (hex, rgb(), or supported named color) |
| `border_width` | int | Border line width in pixels (1-16) |
| `font_size` | int \| `"auto"` | Font size in points. `auto` scales to fill the bounding box. |
| `padding` | int \| [int, int, int, int] | Inner padding in pixels (all sides, or [top, right, bottom, left]) |

> **`source` vs `content`:** Use `source` for live data. Use `content` for static text.
> A widget may use only one. Omitting both renders the widget with an empty value.

---

## Widget Types

### `value`

Displays a numeric value with optional label and unit. Font scales to fill its bounding box
when `font_size: auto`.

```yaml
cpu_temp:
  type: value
  source: cpu.temperature
  label: "CPU Temp"
  unit: "°C"
  precision: 1         # decimal places (default: 0)
  font_size: auto
  color: "#ff6b6b"
```

Additional fields: `unit` (string), `precision` (int, default 0)

---

### `text`

Displays a string value or static content. Wraps text if it exceeds the bounding box width.

```yaml
hostname:
  type: text
  source: system.hostname
  label: "Host"
  font_size: 14
```

---

### `bar`

Horizontal progress bar. Value is clamped to `[min, max]`.

```yaml
ram:
  type: bar
  source: memory.percent
  label: "RAM"
  min: 0
  max: 100
  color: "#4d96ff"
  color_stops:             # optional — overrides color if set
    - [0,  "#6bcb77"]
    - [80, "#ffd93d"]
    - [95, "#ff6b6b"]
```

Additional fields: `min` (float, default 0), `max` (float, default 100), `color_stops`

---

### `gauge`

Tachometer-style arc gauge. Draws a colored arc from `min` to `max` with the current value
as the needle position.

```yaml
cpu:
  type: gauge
  source: cpu.percent
  label: "CPU"
  min: 0
  max: 100
  arc_start: 225    # degrees (default: 225 — bottom-left)
  arc_end: -45      # degrees (default: -45 — bottom-right)
  gauge_ticks: 10   # optional tick marks along the arc
  color_stops:
    - [0,  "#6bcb77"]
    - [70, "#ffd93d"]
    - [90, "#ff6b6b"]
```

Additional fields: `min`, `max`, `arc_start`, `arc_end`, `gauge_ticks`, `color_stops`

---

### `histogram`

Rolling bar chart showing recent history of a value.

Histograms are generic: point `source` at any numeric key in the data store
(`cpu.temperature`, `outside_temp_f`, `custom.sensor_42`, etc.) and the widget
will maintain and draw history for that stream.

```yaml
ram_hist:
  type: histogram
  source: memory.percent
  samples: 60          # number of bars (history length)
  label: "RAM History"
  color: "#4d96ff"
  min: 0
  max: 100
  precision: 1
  unit: "%"
```

Multi-series mode (single cell, multiple live series):

```yaml
net_multi:
  type: histogram
  label: "Net Up/Down (Mb/s)"
  sources:
    - net.recv_mbps
    - net.sent_mbps
  series_labels: ["Dn", "Up"]
  series_colors: ["#22cc88", "#ffaa22"]
  unit: Mb/s
  precision: 2
  min: 0
  max: 200
  samples: 120
```

Additional fields: `samples` (int, default 60), `min`, `max`, `precision`, `unit`, `sources`, `series_labels`, `series_colors`

Note: missing/invalid source values are skipped instead of inserted as zero/min-value bars.

---

### `sparkline`

Rolling line chart showing recent history, no axes.

```yaml
net_in:
  type: sparkline
  source: net.bytes_recv_rate
  samples: 60
  label: "↓ MB/s"
  color: "#6bcb77"
```

Multi-series mode:

```yaml
net_multi:
  type: sparkline
  label: "Net Up/Down (Mb/s)"
  sources:
    - net.recv_mbps
    - net.sent_mbps
  series_labels: ["Dn", "Up"]
  series_colors: ["#22cc88", "#ffaa22"]
  unit: Mb/s
  precision: 2
  min: 0
  max: 200
  samples: 120
```

Additional fields: `samples` (int, default 60), `sources`, `series_labels`, `series_colors`

Note: missing/invalid source values are skipped rather than inserted as zero.

---

### `ups`

Single-card UPS status widget with battery, load, runtime, and input power.

By default it reads from `ups.*` keys. Optionally set `source` to an alternate
prefix namespace.

```yaml
power:
  type: ups
  label: "UPS"
  source: ups
  padding: 8
  color: "#e6edf3"
```

Alias support:
- `type: power.ups` is accepted and normalized to `type: ups`.

---

### `clock`

Live clock, re-rendered every frame.

```yaml
time:
  type: clock
  format: "%H:%M:%S"   # strftime format string
  color: "#ffffff"
  font_size: 32
```

Additional fields: `format` (strftime string, default `"%H:%M:%S"`)

Common `format` examples:

```yaml
# 24-hour time
format: "%H:%M:%S"

# 12-hour time with AM/PM
format: "%I:%M:%S %p"

# Date only
format: "%a %b %d"

# Two-line date + time block
format: "%a %b %d\n%H:%M:%S"
```

---

### `image`

Static image loaded from disk. Scaled to fit the bounding box.

```yaml
logo:
  type: image
  path: "assets/logo.png"    # relative to repo root
  scale: fit                  # fit | fill | stretch (default: fit)
```

Additional fields: `path` (string, required), `scale` (`fit`|`fill`|`stretch`)

---

### `slideshow`

Cycles through a list of images or all images in a directory.

```yaml
bg:
  type: slideshow
  paths:
    - "assets/slideshow/"    # directory — all images in it
  interval: 10               # seconds per image (default: 10)
  scale: fill
  transition: fade           # none | fade (default: none)
```

Additional fields: `paths` (list of file or directory paths), `interval` (seconds),
`transition` (`none`|`fade`)

---

### `panel`

Container widget. Lays out children in a row or column. Supports its own nested `grid`
for table-in-table layouts.

```yaml
header:
  type: panel
  background: "#0d0d1a"
  direction: row         # row | column (default: column)
  align: center          # start | center | end (default: start)
  gap: 8                 # pixels between children (default: 0)
  padding: 8
  children:
    - type: image
      path: "assets/logo.png"
      width: 40
    - type: text
      content: "CASEDD"
      font_size: 28
      color: "#ffffff"
    - type: clock
      format: "%H:%M:%S"
      color: "#888888"
```

Children are inline widget definitions (not named, not in `template_areas`).
Children inherit the panel's bounding box divided by `direction`.

A panel may also use a nested `grid` instead of `direction`:

```yaml
stats_panel:
  type: panel
  grid:
    template_areas: |
      "temp    fan"
      "load    load"
    columns: "1fr 1fr"
    rows: "1fr 1fr"
  children_named:
    temp:
      type: value
      source: cpu.temperature
      unit: "°C"
    fan:
      type: value
      source: cpu.fan_rpm
      unit: "RPM"
    load:
      type: bar
      source: cpu.percent
```

---

## Color formats

Colors accept any of:

| Format | Example |
|--------|---------|
| Hex | `"#1a1a2e"` or `"#fff"` |
| RGB | `"rgb(26, 26, 46)"` |
| Named | `"black"`, `"white"` (standard HTML color names) |

### `color_stops`

A list of `[threshold, color]` pairs. The color is interpolated as the value moves
between thresholds. Thresholds are in the same unit as the widget's `min`/`max` range.

```yaml
color_stops:
  - [0,  "#6bcb77"]   # green below 70
  - [70, "#ffd93d"]   # yellow 70–90
  - [90, "#ff6b6b"]   # red above 90
```

---

## Data store keys (built-in getters)

| Key | Type | Description |
|-----|------|-------------|
| `cpu.percent` | float | CPU usage % (0–100) |
| `cpu.temperature` | float | CPU package temp in °C |
| `cpu.fan_rpm` | float | CPU fan RPM (if readable) |
| `nvidia.percent` | float | GPU usage % |
| `nvidia.temperature` | float | GPU temp in °C |
| `nvidia.memory_used_mb` | float | GPU VRAM used in MB |
| `nvidia.memory_total_mb` | float | GPU VRAM total in MB |
| `memory.percent` | float | RAM usage % |
| `memory.used_gb` | float | RAM used in GB |
| `memory.total_gb` | float | RAM total in GB |
| `disk.percent` | float | Disk usage % (default mount `/`) |
| `net.bytes_recv_rate` | float | Network receive rate in MB/s |
| `net.bytes_sent_rate` | float | Network send rate in MB/s |
| `system.hostname` | str | Machine hostname |
| `system.uptime` | str | Human-readable uptime (e.g. `"3d 4h 12m"`) |
| `system.load_1` | float | 1-minute load average |

External data pushed via Unix socket or REST POST uses the same dotted key namespace.
