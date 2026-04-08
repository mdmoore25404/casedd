# Tuya Smart Device Setup Guide

CASEDD can now poll Tuya smart home devices (temperature/humidity sensors, smart plugs with power monitoring) via local network protocol. This is **READ-ONLY** — no state commands are issued.

## Library Choice: tinytuya

We use **[tinytuya](https://github.com/jasonacox/tinytuya)** for several reasons:

- Pure Python, minimal dependencies
- ~20KB lightweight footprint
- Actively maintained
- Supports local network protocol (important for READ-ONLY polling without cloud dependency)
- No cloud subscription required

Alternative libraries (`tuya-connector-python`, `pytuya`) are either heavier, abandoned, or require paid cloud API subscriptions.

## Prerequisites

1. **Tuya Smart app** installed and at least one device paired
2. **Device IDs and Local Keys** — obtained below
3. **Local network access** to your Tuya devices (Wi-Fi)
4. **tinytuya library** — installed automatically via `pip install -r requirements.txt`

## Getting Device IDs and Local Keys

### Option 1: Extract from Tuya Cloud (Recommended)

tinytuya includes a helper to sync devices from Tuya Cloud:

```bash
source .venv/bin/activate
python -m tinytuya wizard
```

Follow the prompts to:
1. Provide your Tuya Account email/password
2. Select your region (US/EU/CN/IN)
3. Retrieve device IDs and local keys

Output will be saved to `tinytuya/devices.json`.

### Option 2: Extract from Tuya Smart App (Advanced)

1. Open Tuya Smart app
2. Go to **Settings** → **Account and Security**
3. Enable "Device Debugging" or similar (varies by region)
4. In **Device List**, tap each device → note the **ID** field
5. For local keys, use Tuya IoT console or third-party tools (see tinytuya docs)

## Configuration in casedd.yaml

Add devices to your `casedd.yaml` file under the `tuya_devices` key:

```yaml
tuya_devices:
  - device_id: "bf123a56c7890d1e2f3"  # From devices.json or Tuya app
    local_key: "a1b2c3d4e5f6g7h8"     # Your device local key
    device_type: "sensor"              # or "plug"
    ip_address: "192.168.1.50"          # Optional: speeds up polling if known

  - device_id: "ca456b78d9e01f2a3b4"
    local_key: "z9y8x7w6v5u4t3s2"
    device_type: "plug"
    ip_address: "192.168.1.51"

tuya_interval: 10.0  # Poll every 10 seconds (default)
```

### Config Fields Explained

- **device_id**: Unique identifier for the device (36+ hex chars typical)
- **local_key**: Device pairing key (32 hex chars typical)
- **device_type**: `"sensor"` (temperature/humidity) or `"plug"` (power monitoring)
- **ip_address**: (Optional) Local IP address for faster polling. Omit to use mDNS discovery

## Data Store Keys

### Temperature/Humidity Sensors

When a device with `device_type: "sensor"` is polled, the following keys appear in the data store:

- `tuya.sensors.<device_id>.temperature` — float, °C
- `tuya.sensors.<device_id>.humidity` — float, % RH

### Smart Plugs

When a device with `device_type: "plug"` is polled:

- `tuya.plugs.<device_id>.power` — float, watts (W)
- `tuya.plugs.<device_id>.current` — float, milliamps (mA)
- `tuya.plugs.<device_id>.voltage` — float, volts (V)
- `tuya.plugs.<device_id>.energy` — float, kilowatt-hours (kWh) — total cumulative

## Device Protocol Notes (DPS Mapping)

Tuya devices communicate via "Data Points" (DPS). Common assignments:

### Sensors

- DPS 1: Temperature (usually scaled by 10, e.g., 255 = 25.5°C)
- DPS 2: Humidity (%)

### Smart Plugs

- DPS 6: Current (mA)
- DPS 19: Power (W, scaled by 10, e.g., 1500 = 150W)
- DPS 20: Voltage (V, scaled by 10, e.g., 2300 = 230V)
- DPS 26: Total energy (kWh, scaled by 100, e.g., 500 = 5.0kWh)

> **Note**: DPS assignments vary by manufacturer. If your device doesn't populate expected keys, inspect the raw status in logs (set `CASEDD_LOG_LEVEL=DEBUG`) and [file an issue](https://github.com/jasonacox/tinytuya/issues).

## Troubleshooting

### Devices Not Appearing

1. **Check config syntax** — Ensure YAML indentation is correct
2. **Verify local IP** — Device must be on same network as CASEDD host
3. **Check logs**:
   ```bash
   tail -f logs/casedd.log | grep -i tuya
   ```
4. **Restart daemon**:
   ```bash
   ./dev.sh restart
   ```

### "Device offline" Errors

- Device is powered off or disconnected from Wi-Fi
- Local key is incorrect (re-run `tinytuya wizard`)
- Device requires cloud sync (ip_address omitted, slower discovery)
- Firewall blocks local network polling (allow port 6668/UDP if needed)

### Partial Data (Only Some DPS Keys)

This is normal if your device model doesn't expose certain metrics. Check the actual DPS structure:

1. Set `CASEDD_LOG_LEVEL=DEBUG`
2. Restart daemon and wait for first poll
3. Examine logs for raw DPS output
4. Update the parsing logic in [casedd/getters/tuya.py](../casedd/getters/tuya.py) if needed

## Environment Variables (.env)

No environment variables are required for Tuya — all configuration lives in `casedd.yaml`.

The `.env.example` file contains a reference comment showing the YAML format.

## Template Usage

Reference Tuya data in your templates:

```yaml
widgets:
  sensor_row:
    type: panel
    width: 100%
    height: 50px
    children:
      temp_label:
        type: text
        content: "Temp:"
        source: "tuya.sensors.bf123a56c7890d1e2f3.temperature"

  plug_power:
    type: value
    source: "tuya.plugs.ca456b78d9e01f2a3b4.power"
    suffix: " W"
```

## Privacy and Security

- **Local-only**: CASEDD communicates directly with devices on your network (no cloud)
- **Read-only**: No state commands are sent; only polling (fetch) operations
- **Credentials storage**: Device IDs and local keys are stored in `casedd.yaml` (treat as sensitive)
- **No external calls**: Tuya devices do not call out to the internet during polling (verified)

## Performance

- **Default interval**: 10 seconds between polls
- **Per-device overhead**: ~50–100ms latency per device on local network
- **Scaling**: Tested up to 10+ devices without noticeable impact

Adjust `tuya_interval` in `casedd.yaml` to tune:

```yaml
tuya_interval: 5.0   # More frequent polling (5 seconds)
tuya_interval: 30.0  # Less frequent polling (30 seconds)
```

## Further Reading

- [tinytuya GitHub](https://github.com/jasonacox/tinytuya)
- [Tuya IoT Console](https://iot.tuya.com)
- [CASEDD Template Format](template_format.md)
