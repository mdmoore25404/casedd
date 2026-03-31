---
title: Template Format
nav_order: 2
permalink: /template_format/
---

# CASEDD Template Format Specification

Welcome to the CASEDD template docs. This page explains how to define screen layouts and widgets in `.casedd` YAML templates.

Version: 1.0  
File extension: `.casedd`  
Format: YAML

Templates live in the `templates/` directory. The active template is selected via
`CASEDD_TEMPLATE` (environment variable or `casedd.yaml` config key).

Templates are expected to scale to the current panel or framebuffer output.
In normal runtime operation, canvas size comes from the active output device.
`width` and `height` are legacy optional metadata only.

If you want to preserve a design aspect ratio across mismatched displays, set
`aspect_ratio` and `layout_mode: fit`. The layout will be centered and
letterboxed inside the active output instead of being stretched.

---

## Top-level keys

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `name` | string | yes | — | Unique template name (must match filename without extension) |
| `description` | string | no | `""` | Human-readable description |
| `width` | int | no | — | Optional legacy design width; runtime normally uses the active output size |
| `height` | int | no | — | Optional legacy design height; runtime normally uses the active output size |
| `aspect_ratio` | string | no | — | Optional logical layout aspect ratio such as `5:3` or `1.777` |
| `layout_mode` | string | no | `stretch` | `stretch` fills the output, `fit` letterboxes to preserve aspect ratio |
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

### `boolean`

Displays a boolean status as an icon:
- true/on/enabled/1 -> green checkmark
- false/off/disabled/0 -> red slash

```yaml
dns_blocking:
  type: boolean
  source: pihole.blocking.enabled
  label: "Blocking"
  color: "#6de58f"   # true-state color (false stays red)
```

Additional fields: uses common fields only (`label`, `padding`, `background`, `color`).

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

### `table`

Displays newline-delimited two-column rows in a compact table. Each source line
must be formatted as `left|right`.

```yaml
top_domains:
  type: table
  label: "Top Blocked Domains"
  source: pihole.top_blocked.list
  font_size: auto
  max_items: 5
```

Additional fields: `max_items` (optional row cap).

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

#### Metric-driven image selection (`tiers`)

The `image` widget can automatically swap its image based on live data-store
values.  This is useful for mascot images or status icons that should escalate
visually as load increases.

```yaml
mascot:
  type: image
  path: assets/mascot-calm.png     # shown when no tier fires
  scale: fit
  tiers:
    - path: assets/mascot-stressed.png
      when:
        - { source: cpu.percent,    operator: gte, value: 50 }
        - { source: memory.percent, operator: gte, value: 60 }
    - path: assets/mascot-angry.png
      when:
        - { source: cpu.percent,    operator: gte, value: 75 }
        - { source: memory.percent, operator: gte, value: 82 }
    - path: assets/mascot-fire.png
      when:
        - { source: cpu.percent,    operator: gte, value: 90 }
        - { source: memory.percent, operator: gte, value: 92 }
```

**Evaluation rules:**

- Tiers are listed in **ascending severity** (lowest first).
- The engine evaluates from **highest tier to lowest**; the first matching tier wins.
- Within a tier's `when` list, semantics are **OR** — any single condition firing
  is enough to activate that tier.
- A data key **absent from the store** evaluates to `False` — the tier stays
  inactive until data arrives.
- The base `path` is used when **no tier fires** (calm / idle state).

**`tiers[].when` condition fields:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `source` | string | required | Dotted data-store key (e.g. `cpu.percent`) |
| `operator` | string | `gte` | `gt`, `gte`, `lt`, `lte`, `eq`, `neq` |
| `value` | number \| string | `0` | Threshold to compare against |

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

### `plex_now_playing`

Compact table of active Plex sessions.

Input rows must come from `plex.sessions.rows` with format:
`USER|TITLE|MEDIA_TYPE|PROGRESS_PERCENT|TRANSCODE_DECISION`

```yaml
now:
  type: plex_now_playing
  label: "Now Playing"
  source: plex.sessions.rows
  color: "#7ce29f"
  filter_regex: "(kids|private)"   # optional privacy filter
```

Additional fields: `filter_regex` (optional Python regex to hide matching rows)
and `max_items` (optional row cap for displayed items)

---

### `plex_recently_added`

Compact table of recently-added Plex media.

Input rows must come from `plex.recently_added.rows` with format:
`LIBRARY|TITLE`

```yaml
recent:
  type: plex_recently_added
  label: "Recently Added"
  source: plex.recently_added.rows
  color: "#89b7ff"
  background: "#0f1a24"          # optional section background
  max_items: 10                    # optional row cap
  filter_regex: "(kids|family)"   # optional privacy filter
```

Additional fields: `filter_regex` (optional Python regex to hide matching rows)
and `max_items` (optional row cap for displayed items)

`plex_dashboard.casedd` also demonstrates template-level `skip_if` so the
template is automatically skipped in rotation when both
`plex.sessions.active_count` and `plex.sessions.transcoding_count` are zero.

For stronger Plex branding, the dashboard header can use a nested `panel.grid`
area with an `image` widget (`assets/plex/plex-logo.png`) so the logo scales
within its own header cell while stat widgets keep stable space.

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

---

## Template triggers (casedd.yaml)

Trigger rules are defined in `casedd.yaml` (not inside `.casedd` template files).
When a trigger activates, CASEDD switches to the target template and draws an
alert border around the frame to indicate the forced condition.

### Trigger rule fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `source` | string | yes | — | Dotted data-store key to watch (e.g. `cpu.percent`) |
| `operator` | string | no | `gte` | Comparison: `gt`, `gte`, `lt`, `lte`, `eq`, `neq` |
| `value` | number/string | yes | — | Threshold to compare against |
| `template` | string | yes | — | Template to activate when condition is met |
| `duration` | float | no | `0` | Seconds condition must stay true before activating |
| `hold_for` | float | no | `0` | Minimum seconds to keep template active once triggered |
| `clear_operator` | string | no | — | Explicit operator for the clear condition (must set together with `clear_value`) |
| `clear_value` | number | no | — | Threshold to release the trigger (default: inverts the activate condition) |
| `cooldown` | float | no | `0` | Seconds before the same rule may fire again after clearing |
| `priority` | int 0–1000 | no | `100` | Lower = higher priority; wins when multiple triggers fire at once |
| `notify` | bool | no | `false` | Send a Pushover notification when the trigger first activates |
| `notify_title` | string | no | auto | Custom notification title |
| `notify_message` | string | no | auto | Custom notification body |
| `disabled` | bool | no | `false` | When `true`, rule is ignored without being deleted — use to toggle rules temporarily |

### OR-logic (multiple rules → same template)

Defining multiple rules that all target the same `template` creates OR semantics:
any one rule activating switches to that template. Each rule manages its own
`hold_for` / `clear` / `cooldown` state independently.

```yaml
# Any of these three conditions activates the nvidia_detail view.
template_triggers:
  - source: nvidia.percent
    operator: gte
    value: 50
    template: nvidia_detail
    duration: 5
    hold_for: 30
    clear_operator: lt
    clear_value: 40
    cooldown: 60
    priority: 10

  - source: nvidia.memory_percent
    operator: gte
    value: 90
    template: nvidia_detail
    duration: 5
    hold_for: 30
    clear_operator: lt
    clear_value: 80
    cooldown: 60
    priority: 10

  - source: nvidia.temperature
    operator: gte
    value: 80
    template: nvidia_detail
    duration: 5
    hold_for: 30
    clear_operator: lt
    clear_value: 70
    cooldown: 60
    priority: 10
    # Disable this individual rule without removing it:
    disabled: true
```

### Disabling a trigger rule

Set `disabled: true` on any rule to pause it without deleting the config entry.
Useful when debugging, testing, or temporarily suppressing a noisy trigger:

```yaml
template_triggers:
  - source: cpu.percent
    operator: gte
    value: 80
    template: htop
    hold_for: 20
    cooldown: 60
    priority: 10
    disabled: true   # paused — rule is validated but never evaluated
```

### Alert border color

When a trigger holds the display, CASEDD draws a border around the frame.
Configure the color globally in `casedd.yaml`:

```yaml
# Default: bright red.  Any CSS color string is accepted.
trigger_border_color: "#dc1e1e"

# Alternatives:
# trigger_border_color: "#ff00ff"   # magenta / fuchsia (red-green colorblind safe)
# trigger_border_color: "#ff8c00"   # dark orange
# trigger_border_color: "rgb(0, 180, 255)"  # bright blue
```

The environment variable `CASEDD_TRIGGER_BORDER_COLOR` is also supported.
