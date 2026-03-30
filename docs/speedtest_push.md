# Pushing Speed Test Results to CASEDD

CASEDD can display speed test metrics collected on **any machine** on your network.
This is useful when the machine running CASEDD has a limited network link that would
artificially cap test results (e.g. a 1 Gb/s server with a 2 Gb/s internet plan).

---

## 1. Enable passive mode on CASEDD

In your `casedd.yaml` (or equivalent environment variables), disable the local
Ookla CLI poller and set your plan's advertised speeds so that
percentage-of-plan gauges are calibrated correctly:

```yaml
# casedd.yaml
speedtest_passive: true                # disable local CLI runner
speedtest_advertised_down_mbps: 2000   # your ISP plan download speed in Mb/s
speedtest_advertised_up_mbps: 2000     # your ISP plan upload speed in Mb/s
# Optional: cap the percentage gauge at your bottleneck link speed
# speedtest_reference_down_mbps: 1000 # e.g. 1Gb/s NIC limit
# speedtest_reference_up_mbps: 1000
```

Or via environment variables:

```bash
CASEDD_SPEEDTEST_PASSIVE=true
CASEDD_SPEEDTEST_ADVERTISED_DOWN_MBPS=2000
CASEDD_SPEEDTEST_ADVERTISED_UP_MBPS=2000
```

---

## 2. Push endpoint

```
POST http://<casedd-host>:8080/api/update
Content-Type: application/json
```

The request body is a JSON object with a single `"update"` key whose value is a
flat mapping of dotted data-store keys to values:

```json
{
  "update": {
    "<key>": <value>,
    ...
  }
}
```

- Keys must be dotted-namespace strings (e.g. `speedtest.download_mbps`).
- Values must be `float`, `int`, or `str`. Nested objects and `null` are rejected
  with HTTP 422.
- A successful push returns **HTTP 204 No Content**.

---

## 3. Speedtest data-store keys

Push any subset of these keys.  CASEDD will display whatever is present.

| Key | Type | Description |
|-----|------|-------------|
| `speedtest.download_mbps` | `float` | Raw download speed in Mb/s |
| `speedtest.upload_mbps` | `float` | Raw upload speed in Mb/s |
| `speedtest.ping_ms` | `float` | Latency in milliseconds |
| `speedtest.jitter_ms` | `float` | Jitter in milliseconds |
| `speedtest.download_pct_ref` | `float` | Download as % of reference/plan speed |
| `speedtest.upload_pct_ref` | `float` | Upload as % of reference/plan speed |
| `speedtest.download_pct_adv` | `float` | Download as % of advertised plan (optional) |
| `speedtest.upload_pct_adv` | `float` | Upload as % of advertised plan (optional) |
| `speedtest.download_status` | `str` | `good`, `marginal`, or `critical` |
| `speedtest.upload_status` | `str` | `good`, `marginal`, or `critical` |
| `speedtest.simple_summary` | `str` | Human-readable one-liner, e.g. `"1847 / 1923 Mb/s"` |
| `speedtest.summary` | `str` | Verbose summary with status and ping |
| `speedtest.last_run` | `str` | Timestamp of the test, e.g. `"2026-03-29 14:32:00"` — **auto-filled by CASEDD** if absent |
| `speedtest.server_name` | `str` | Speedtest server name (optional) |
| `speedtest.server_location` | `str` | Speedtest server location (optional) |
| `speedtest.server_country` | `str` | Speedtest server country (optional) |
| `speedtest.server_host` | `str` | Speedtest server hostname (optional) |
| `speedtest.server_id` | `str` | Ookla server ID (optional) |

> **Minimum required for the speedtest template to be useful:**
> `download_mbps`, `upload_mbps`, `ping_ms`, `jitter_ms`, `download_pct_ref`,
> `upload_pct_ref`, and `simple_summary`.

> **Auto-filled fields:** When any `speedtest.*` key is pushed via `POST /api/update`
> and `speedtest.last_run` is absent, CASEDD automatically records the server's
> current local time (format: `"YYYY-MM-DD HH:MM:SS"`).  You do **not** need to
> include `speedtest.last_run` in your push payload unless you want to override the
> timestamp with a value from the remote machine.

---

## 4. Computing percentage-of-plan

If your pushing machine knows the plan speeds you can compute the percentage fields:

```python
advertised_down_mbps = 2000.0   # your plan
advertised_up_mbps   = 2000.0

download_pct_ref = (download_mbps / advertised_down_mbps) * 100.0
upload_pct_ref   = (upload_mbps   / advertised_up_mbps)   * 100.0
```

If you have a bottleneck link (e.g. 1 Gb/s NIC) use that as the reference
instead so the gauge reads 100% when you saturate your link:

```python
reference_down_mbps  = 1000.0   # NIC limit
download_pct_ref     = (download_mbps / reference_down_mbps) * 100.0
```

---

## 5. Example: curl

```bash
CASEDD_HOST=192.168.1.17

curl -s -X POST "http://${CASEDD_HOST}:8080/api/update" \
  -H "Content-Type: application/json" \
  -d '{
    "update": {
      "speedtest.download_mbps":   1847.3,
      "speedtest.upload_mbps":     1923.1,
      "speedtest.ping_ms":         4.2,
      "speedtest.jitter_ms":       0.8,
      "speedtest.download_pct_ref": 92.4,
      "speedtest.upload_pct_ref":   96.2,
      "speedtest.download_status": "good",
      "speedtest.upload_status":   "good",
      "speedtest.simple_summary":  "1847 / 1923 Mb\/s",
      "speedtest.last_run":        "2026-03-29 14:32:00"
    }
  }'
```

A `204` response means the data was accepted.

---

## 6. Example: Python (Ookla CLI output → CASEDD push)

Run this on the machine that has the fast NIC.  It calls the Ookla `speedtest`
binary, parses the JSON output, and POSTs the derived metrics to CASEDD.

```python
#!/usr/bin/env python3
"""push_speedtest.py — run speedtest and push results to CASEDD."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import httpx  # pip install httpx

CASEDD_URL = "http://192.168.1.17:8080/api/update"
ADVERTISED_DOWN_MBPS = 2000.0
ADVERTISED_UP_MBPS   = 2000.0
MEGABIT = 1_000_000.0


def run_speedtest() -> dict[str, object]:
    result = subprocess.run(
        ["speedtest", "--accept-license", "--accept-gdpr", "--format=json"],
        capture_output=True, text=True, timeout=180, check=True,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def status(ratio: float) -> str:
    if ratio < 0.7:
        return "critical"
    if ratio < 0.9:
        return "marginal"
    return "good"


def build_payload(raw: dict[str, object]) -> dict[str, object]:
    dl_bw   = float(raw["download"]["bandwidth"])   # bytes/s  # type: ignore[index]
    ul_bw   = float(raw["upload"]["bandwidth"])     # type: ignore[index]
    ping    = float(raw["ping"]["latency"])          # type: ignore[index]
    jitter  = float(raw["ping"]["jitter"])           # type: ignore[index]

    dl_mbps = (dl_bw * 8) / MEGABIT
    ul_mbps = (ul_bw * 8) / MEGABIT
    dl_pct  = (dl_mbps / ADVERTISED_DOWN_MBPS) * 100.0
    ul_pct  = (ul_mbps / ADVERTISED_UP_MBPS)   * 100.0

    return {
        "speedtest.download_mbps":    round(dl_mbps, 2),
        "speedtest.upload_mbps":      round(ul_mbps, 2),
        "speedtest.ping_ms":          round(ping, 2),
        "speedtest.jitter_ms":        round(jitter, 2),
        "speedtest.download_pct_ref": round(dl_pct, 2),
        "speedtest.upload_pct_ref":   round(ul_pct, 2),
        "speedtest.download_status":  status(dl_mbps / ADVERTISED_DOWN_MBPS),
        "speedtest.upload_status":    status(ul_mbps / ADVERTISED_UP_MBPS),
        "speedtest.simple_summary":   f"{dl_mbps:.0f} / {ul_mbps:.0f} Mb/s",
        "speedtest.last_run": datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    raw = run_speedtest()
    payload = build_payload(raw)
    resp = httpx.post(CASEDD_URL, json={"update": payload}, timeout=10)
    resp.raise_for_status()
    print(f"Pushed: {payload['speedtest.simple_summary']}")
```

Schedule this with `cron` on the speed-test machine:

```cron
# Run speedtest every 30 minutes and push to CASEDD
*/30 * * * * /usr/bin/python3 /opt/push_speedtest.py >> /var/log/push_speedtest.log 2>&1
```

---

## 7. Template

Activate the dedicated speedtest template on demand via the web UI, or add it
to a rotation:

```yaml
# casedd.yaml
template_rotation:
  - system_stats
  - speedtest
template_rotation_interval: 30  # seconds per template
```

Or force it permanently:

```yaml
template: speedtest
```
