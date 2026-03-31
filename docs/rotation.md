# Template Rotation

CASEDD can automatically cycle through multiple templates — useful for showing different dashboards in sequence on a single display. This page explains how rotation works and how to configure it.

---

## How Rotation Works

Rotation is controlled by `template_rotation_enabled` in `casedd.yaml`.

- When `template_rotation_enabled: true`, CASEDD cycles through
  `template_rotation` entries in order.
- When `template_rotation_enabled: false`, CASEDD stays on the base
  `template` (unless schedule/trigger rules override it).

`template` is **not** automatically prepended to `template_rotation`. Add it
explicitly when you want it included in the cycle.

When you add more templates to the rotation list:

1. The panel displays the **current entry** for its configured **dwell time**.
2. When the dwell time expires (or a skip condition fires), CASEDD advances to the **next entry**.
3. After the last entry, it wraps back to the first and repeats.

Rotation is evaluated every render tick, so skip conditions respond in near real-time.

### Priority Order

Template selection respects a strict priority chain. Rotation is the **lowest-priority** policy — it only activates when nothing higher-priority applies:

| Priority | Policy | Description |
|---|---|---|
| 1 | **Force override** | A data-store key (`casedd.template.force.<panel>`) forces a specific template. |
| 2 | **Trigger rules** | Metric-driven rules (e.g., "show `alerts` when GPU temp > 90 °C"). |
| 3 | **Schedule rules** | Time-of-day rules (e.g., "show `clock` between 22:00–07:00"). |
| 4 | **Rotation** | Automatic cycling through the configured template list. |

---

## Configuring Rotation via the Advanced App

Open the **Advanced App** (`/app/`) and select a panel. The **Template Rotation** card lets you:

| Setting | Description |
|---|---|
| **Rotation enabled** | Toggles whether rotation is active for the panel. |
| **Default dwell (s)** | How long, in seconds, to stay on each template unless overridden per-entry. |
| **Template** | Which `.casedd` template to include in the cycle. |
| **Dwell (s)** | Per-entry dwell override. Leave blank to use the default dwell. |
| **Skip if…** | An optional data-store condition. When the condition matches, this entry is skipped automatically. |

Click **Save Rotation** to apply and persist the rotation configuration. It survives daemon restarts.

---

## Dwell Times

The **default dwell** applies to every entry that doesn't have its own per-entry dwell set.

**Example:** show `system_stats` for 30 s, then `speedtest` for 5 s:

| Template | Dwell |
|---|---|
| `system_stats` | *(default: 30 s)* |
| `speedtest` | 5 s |

Set *Default dwell* to `30`, then set the `speedtest` entry's dwell to `5`.

---

## Skip Conditions

A skip condition causes a rotation entry to be **skipped** (jumped over) when the condition is true. The entry remains in the list and is re-evaluated on every cycle — it will appear again as soon as the condition clears.

### When a template is skipped

- The rotation advances immediately to the next non-skipped entry.
- If **all** entries are skippable at the same time, the **current entry is held** rather than leaving the display blank.
- When a skipped entry's condition clears, it will be shown on the next cycle pass.

### Missing data = skip

If the **source key** for a skip condition is not present in the data store (e.g., the getter hasn't run yet, or a push hasn't arrived), the condition evaluates to **true** — meaning the template is skipped until data arrives. This prevents showing a template with blank/stale values.

### Condition operators

| Operator | Meaning | Example |
|---|---|---|
| `lte` (default) | skip if value ≤ threshold | `cpu.percent lte 10` |
| `lt` | skip if value < threshold | `cpu.percent lt 10` |
| `gte` | skip if value ≥ threshold | `cpu.temperature gte 80` |
| `gt` | skip if value > threshold | `cpu.temperature gt 90` |
| `eq` | skip if value == threshold | `system.hostname eq mymachine` |
| `neq` | skip if value ≠ threshold | `system.hostname neq mymachine` |

### AND semantics

When multiple skip conditions are added to one entry, **all** conditions must be true for the entry to be skipped (AND logic). To express OR logic, create separate rotation entries with separate conditions.

---

## Worked Example

**Goal:** cycle between `system_stats` and `htop`, but only show `htop` when the system is busy (CPU > 10 %).

| Template | Dwell | Skip if… |
|---|---|---|
| `system_stats` | *(default: 30 s)* | *(none — always shown)* |
| `htop` | *(default: 30 s)* | `cpu.percent` `lte` `10` |

When CPU usage is ≤ 10 %, `htop` is silently skipped and `system_stats` repeats. When CPU climbs above 10 %, `htop` rejoins the cycle.

**API equivalent (PUT `/api/panels/primary/rotation`):**

```json
{
  "rotation_interval": 30,
  "rotation_entries": [
    { "template": "system_stats" },
    {
      "template": "htop",
      "skip_if": [{ "source": "cpu.percent", "operator": "lte", "value": 10 }]
    }
  ]
}
```

---

## Template-Level Skip Conditions (`.casedd` files)

In addition to per-entry skip conditions set in the UI, you can bake a **default skip condition** directly into a `.casedd` template file using the top-level `skip_if` key:

```yaml
name: htop
description: Process list.
skip_if:
  - source: cpu.percent
    operator: lte
    value: 10
# ... rest of template
```

### Priority rule

> **Rotation-level skip conditions always win.**  
> Template-level `skip_if` is used only as a fallback when the corresponding rotation entry has **no** entry-level skip conditions.

| Source | Used when… |
|---|---|
| Rotation entry `skip_if` | The entry has one or more conditions set (via the UI or API). |
| Template file `skip_if` | The rotation entry has **no** skip conditions. |

This lets you ship sensible defaults with a template (e.g., "only show me when busy") while still allowing full override from the UI without touching the template file.

---

## Persistence

Rotation configuration set via the Advanced App or the API is
**automatically saved** to `casedd.yaml` and reloaded on startup.

---

## REST API Reference

### Get current rotation

```
GET /api/panels/{panel}/rotation
```

Returns the current rotation config including all entries, default dwell interval, and the panel's base template.

### Update rotation

```
PUT /api/panels/{panel}/rotation
Content-Type: application/json

{
  "rotation_enabled": true,
  "rotation_interval": 30,
  "rotation_entries": [
    { "template": "system_stats" },
    { "template": "speedtest", "seconds": 5,
      "skip_if": [{ "source": "speedtest.download_mbps", "operator": "lte", "value": 0 }] }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `rotation_enabled` | bool | Enables/disables rotation. `false` pins to base template. |
| `rotation_interval` | float | Default dwell in seconds (applies to entries without `seconds`). |
| `rotation_entries` | list | Ordered list of rotation entries. |
| `rotation_entries[].template` | string | Template name. |
| `rotation_entries[].seconds` | float \| null | Per-entry dwell override. `null` = use default. |
| `rotation_entries[].skip_if` | list | Skip conditions (all must match to skip). |
| `rotation_entries[].skip_if[].source` | string | Data-store key to compare. |
| `rotation_entries[].skip_if[].operator` | string | `gt`, `gte`, `lt`, `lte`, `eq`, `neq` (default: `lte`). |
| `rotation_entries[].skip_if[].value` | number \| string | Comparison threshold. |
