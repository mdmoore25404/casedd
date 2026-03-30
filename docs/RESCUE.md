**Rescue & display policy**

- Keep local `getty@tty1` enabled so a local keyboard will always provide a login
  console. Masking getty prevents local rescue unless you use GRUB/serial/live media.

- The `fb_unblank` daemon blanks `/sys/class/graphics/fb0/blank` by default and
  unblanks it on local input (keyboard/mouse). This allows CASEDD to own the
  small in-case HDMI display most of the time while still allowing you to use the
  display interactively when needed.

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
- `IDLE_SECONDS` (env): seconds of inactivity before the screen is re-blanked (default: `60`).
- `FB_BLANK_PATH` (env): sysfs path to the framebuffer `blank` file (default: `/sys/class/graphics/fb0/blank`).
- `FB_DISABLE_CONSOLE` (env): when truthy (default: `1`) the daemon will attempt to disable the kernel framebuffer console for the device while blanked.
- `FB_KEEP_PATH` (env): when the file at this path exists the daemon will not re-blank the display (default: `/run/casedd/keep-unblank`). This is useful to keep the last image visible during remote tests.

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

echo 0 | sudo tee /sys/class/graphics/fb0/blank
Recovery quick commands if you are locked out:

```bash
# Re-enable login on tty1
sudo systemctl unmask --now getty@tty1.service
sudo systemctl enable --now getty@tty1.service
# Unblank the framebuffer
echo 0 | sudo tee /sys/class/graphics/fb0/blank
```

Keeping the last image visible during tests

By default the daemon will re-blank after `IDLE_SECONDS` (60s) — the last
static image written by `scripts/fb_test.py` will remain visible until that
timeout expires. To prevent re-blanking while you run tests, create the
keep-file used by the daemon (requires sudo):

```bash
sudo mkdir -p /run/casedd
sudo touch /run/casedd/keep-unblank
# run your tests (they will keep the screen unblanked)
sudo rm /run/casedd/keep-unblank
```

Warnings & notes
- Disabling the kernel framebuffer console (`FB_DISABLE_CONSOLE=1`) is a
  best-effort operation and may not be supported by all drivers. If you
  experience problems, restore the console with the recovery commands above.
- The daemon requires access to `/dev/input/event*` devices and the
  framebuffer sysfs file; run it as root via systemd as shown above.

Serial console / GRUB rescue notes
- Enable provider/VM serial console or add `console=ttyS0,115200` to GRUB to get
  a rescue shell remotely. Use `systemd.unit=rescue.target` in GRUB to boot to
  a recovery shell.
