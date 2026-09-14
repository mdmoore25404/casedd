**Rescue & display policy**

- If the recovered login prompt appears rotated/upside-down or shows a
  blinking cursor the panel mounting doesn't call for, see
  [HOST_DISPLAY_SETUP.md](HOST_DISPLAY_SETUP.md) for the host-level
  `fbcon=rotate:N` / `vt.global_cursor_default=0` kernel settings — these
  must be reapplied after any fresh OS install.

- Keep local `getty@tty1` enabled so a local keyboard will always provide a login
  console. Masking getty prevents local rescue unless you use GRUB/serial/live media.

- The `fb_unblank` daemon keeps `/sys/class/graphics/fb0/blank` powered on
  (unblanked) at all times so CASEDD's rendered frames are always visible. On
  genuine local keyboard/mouse/touch input it enables the kernel framebuffer
  console overlay (`console` sysfs attribute) so you can use the physical
  display interactively (login prompt, etc.); after `IDLE_SECONDS` of no
  further activity it hides the console overlay again so CASEDD's frames
  reappear. The panel itself is never powered off unless you explicitly opt
  into the legacy `FB_BLANK_ON_IDLE=1` behaviour (see below). Devices that
  only expose switch-sense capabilities (e.g. HDMI/DisplayPort audio
  jack-detect nodes on multi-GPU hosts) are ignored as activity sources,
  since they fire spontaneously and are not genuine local input.

Installing the unblank daemon (example):

```bash
# Activate venv and install dependency
source .venv/bin/activate
pip install evdev

# Copy service to system locations (requires sudo)
sudo cp scripts/fb_unblank_daemon.py /usr/local/bin/
sudo chmod +x /usr/local/bin/fb_unblank_daemon.py
sudo cp deploy/fb-unblank.service /etc/systemd/system/

# Start and enable at boot
sudo systemctl daemon-reload
sudo systemctl enable --now fb-unblank.service
```

Testing manually (without installing):

```bash
# Run from repo using venv as root so it can access input devices and sysfs
sudo env PYTHONPATH=. /home/$(whoami)/casedd/.venv/bin/python3 scripts/fb_unblank_daemon.py
```

Runtime configuration and behaviour
- `IDLE_SECONDS` (env): seconds of inactivity before the escape-hatch console is hidden again (default: `60`).
- `FB_BLANK_PATH` (env): sysfs path to the framebuffer `blank` file (default: `/sys/class/graphics/fb0/blank`).
- `FB_DISABLE_CONSOLE` (env): when truthy (default: `1`) the daemon will toggle the kernel framebuffer console for the device as the escape hatch activates/deactivates.
- `FB_KEEP_PATH` (env): when the file at this path exists the daemon will not hide the console overlay again (default: `/run/casedd/keep-unblank`). This is useful to keep a manually-shown console visible during remote tests.
- `FB_BLANK_ON_IDLE` (env): legacy opt-in (default: `0`/off). When truthy, the daemon will additionally power off the panel (`blank=1`) after `IDLE_SECONDS`, matching the daemon's historical default behaviour. Leave this off for normal CASEDD operation -- powering off the panel also hides CASEDD's own rendered frames, which is virtually never what you want on a case-display host.

Examples (systemd drop-in to change idle timeout or behaviour):

```ini
[Service]
Environment=IDLE_SECONDS=300
Environment=FB_DISABLE_CONSOLE=1
Environment=FB_KEEP_PATH=/run/casedd/keep-unblank
```

Create a drop-in at `/etc/systemd/system/fb-unblank.service.d/override.conf`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart fb-unblank.service
```

Recovery quick commands if you are locked out:

```bash
# Re-enable login on tty1
sudo systemctl unmask --now getty@tty1.service
sudo systemctl enable --now getty@tty1.service
# Unblank the framebuffer (should already be 0 under normal operation)
echo 0 | sudo tee /sys/class/graphics/fb0/blank
# Show the kernel console overlay directly if you need a login prompt now
echo 1 | sudo tee /sys/class/graphics/fb0/console
```

Keeping the escape-hatch console visible during tests

By default the daemon will hide the console overlay again after
`IDLE_SECONDS` (60s) once shown by local input. To prevent it from hiding
while you run tests, create the keep-file used by the daemon (requires
sudo):

```bash
sudo mkdir -p /run/casedd
sudo touch /run/casedd/keep-unblank
# run your tests (the console overlay will stay visible)
sudo rm /run/casedd/keep-unblank
```

Warnings & notes
- Toggling the kernel framebuffer console (`FB_DISABLE_CONSOLE=1`) is a
  best-effort operation and may not be supported by all drivers. If you
  experience problems, restore the console with the recovery commands above.
- The daemon requires access to `/dev/input/event*` devices and the
  framebuffer sysfs file; run it as root via systemd as shown above.
- If the physical display appears to go fully blank/black with no CASEDD
  content and no console, check `cat /sys/class/graphics/fb0/blank` -- it
  should read `0`. A value of `1` means something (or a stale
  `FB_BLANK_ON_IDLE=1` override) has powered the panel off; the daemon
  forces this back to `0` on every startup, so restarting
  `fb-unblank.service` (or `echo 0 | sudo tee /sys/class/graphics/fb0/blank`)
  will recover it immediately.

Serial console / GRUB rescue notes
- Enable provider/VM serial console or add `console=ttyS0,115200` to GRUB to get
  a rescue shell remotely. Use `systemd.unit=rescue.target` in GRUB to boot to
  a recovery shell.
