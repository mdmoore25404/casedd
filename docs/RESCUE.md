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

Recovery quick commands if you are locked out:

```bash
# Re-enable login on tty1
sudo systemctl unmask --now getty@tty1.service
sudo systemctl enable --now getty@tty1.service
# Unblank the framebuffer
echo 0 | sudo tee /sys/class/graphics/fb0/blank
```

Serial console / GRUB rescue notes
- Enable provider/VM serial console or add `console=ttyS0,115200` to GRUB to get
  a rescue shell remotely. Use `systemd.unit=rescue.target` in GRUB to boot to
  a recovery shell.
