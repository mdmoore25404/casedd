# Template Snapshot Fixtures

Generic, privacy-safe test data for templates excluded from automated snapshot
generation due to sensitive data concerns (Plex, InvokeAI, NZBGet, Pi-hole,
Servarr). Each file is a standard CASEDD replay payload that can be consumed
two ways:

1. **Web UI test mode** — paste or load the file content in the Replay JSON
   textarea under Test Mode to drive the template with deterministic demo data.

2. **`capture_template_snaps.py` with `--fixture-dir`** — the capture script
   auto-detects matching fixtures and pushes data before capturing, saving the
   result as `{template}_demo.png` (gitignore-allowed naming).

## Files

| File | Template | Keys pushed |
|---|---|---|
| `plex_dashboard.json` | `plex_dashboard` | `plex.*` |
| `invokeai.json` | `invokeai` | `invokeai.*` |
| `nzbget_dashboard.json` | `nzbget_dashboard` | `nzbget.*` |
| `pihole.json` | `pihole` | `pihole.*` |
| `servarr_dashboard.json` | `servarr_dashboard` | `radarr.*`, `sonarr.*` |

## Replay format

Each file conforms to the CASEDD replay API schema:

```json
{
  "records": [
    {
      "at_ms": 0,
      "update": {
        "some.key": 42.0
      }
    }
  ],
  "loop": false,
  "speed": 1.0
}
```

The `update` object uses the same dotted key namespace as the getter that would
normally populate the store.  Values are `float`, `int`, or `str` — the same
types the store accepts.

## Usage: web UI test mode

1. Start CASEDD in dev mode: `./dev.sh start`
2. Open the web UI at `http://localhost:18080`
3. Enter **Test Mode**, select **Replay**, paste the contents of the fixture
   file, and click **Start Replay**.
4. Switch the display to the corresponding template.
5. Take a screenshot via the `/image` endpoint or the web UI preview.

## Usage: capture script

```bash
# Capture all templates, using fixtures for blacklisted ones:
python -m scripts.capture_template_snaps \
  --all \
  --fixture-dir scripts/fixtures \
  --url http://localhost:18080

# Capture a single fixture-driven template:
python -m scripts.capture_template_snaps \
  --template plex_dashboard \
  --fixture-dir scripts/fixtures \
  --url http://localhost:18080
```

Fixture-based captures are saved as `{template}_demo.png` so they pass the
`.gitignore` negation rule `!*_demo*.png` and can be committed to the repo.

## Data conventions

- **No real hostnames, IPs, or usernames** — use generic labels like `alice`,
  `192.168.1.10`, `Demo Plex Server`.
- **Realistic magnitudes** — values should look plausible on the rendered
  widget (e.g. download speed in MB/s, not GB/s).
- **Table row format** — matches the exact pipe-delimited format each getter
  emits.  See the relevant getter docstring for the column order.
- **Deterministic** — fixtures contain a single `at_ms: 0` record.  They are
  not animated; the goal is a stable screenshot, not a live demo.
