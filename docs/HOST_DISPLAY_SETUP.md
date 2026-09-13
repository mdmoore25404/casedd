Host OS display setup (panel-as-monitor)
=========================================

Summary
-------
CASEDD's small in-case panel is physically mounted rotated, and it is also
used as a real Linux console/monitor (local login prompt, `getty`) outside of
CASEDD's own rendering. Two host-level (kernel) settings are required so the
**OS-level** output — not just CASEDD's own frames — displays correctly:

1. **Rotate the kernel framebuffer console 180°** so the text-mode login
   prompt (and any other kernel console output) renders right-side up on the
   physically-rotated panel.
2. **Disable the blinking VT cursor globally** so the login prompt's cursor
   doesn't blink on top of the panel when no getty is actively focused,
   independent of CASEDD's own per-session cursor suppression (see
   `deploy/casedd.service`, which hides the cursor only while CASEDD itself
   is running).

These are **host OS** settings — they must be reapplied after a fresh OS
install/reimage. They are not part of `deploy/install/install.sh` because
they touch the bootloader and require a reboot; apply them once per machine.

Why this is separate from CASEDD's own rotation
------------------------------------------------
`casedd.yaml`'s `fb_rotation` (or `CASEDD_FB_ROTATION`) only rotates the
frames CASEDD itself writes via direct framebuffer `mmap`. It has no effect
on the kernel's own text console (login prompt, dmesg, `systemd` boot
messages) because CASEDD writes raw pixels straight to the framebuffer,
bypassing the kernel's `fbcon` text renderer entirely. If the panel is
mounted upside-down (or rotated any other way), the kernel console must be
rotated independently at the OS level using the settings below.

Applying the fix
-----------------
Edit `/etc/default/grub` and set (adjust the rotate value for your panel's
physical orientation — see **Rotation values** below):

```bash
sudo sed -i \
  's|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT="fbcon=rotate:2 vt.global_cursor_default=0"|' \
  /etc/default/grub
sudo update-grub
sudo reboot
```

- `fbcon=rotate:2` — rotates the kernel framebuffer console 180°. This is
  what makes the local login prompt (and any other kernel-console text)
  display right-side up on a panel mounted upside-down.
- `vt.global_cursor_default=0` — disables the blinking text-mode cursor on
  all virtual terminals by default at boot. This complements (does not
  replace) `deploy/casedd.service`'s `ExecStartPre` cursor-blink toggle,
  which only takes effect while the CASEDD service itself is active.

Rotation values
---------------
`fbcon=rotate:N` accepts:

| N | Rotation |
|---|----------|
| 0 | Normal (no rotation) |
| 1 | 90° clockwise |
| 2 | 180° (upside-down panel) |
| 3 | 270° clockwise (90° counter-clockwise) |

This should normally match the same physical orientation used for
`fb_rotation` / `CASEDD_FB_ROTATION` in `casedd.yaml`, since both the kernel
console and CASEDD's own frames are being displayed on the same
physically-mounted panel.

Testing without rebooting
--------------------------
Both settings have live sysfs equivalents under `/sys/class/graphics/fbcon/`
that take effect immediately (useful for testing the correct rotation value
before persisting it via GRUB and rebooting):

```bash
# Rotate console output live (0-3, see table above)
echo 2 | sudo tee /sys/class/graphics/fbcon/rotate
# Apply the same rotation to every allocated console, not just the active one
echo 2 | sudo tee /sys/class/graphics/fbcon/rotate_all
# Disable the blinking cursor live
echo 0 | sudo tee /sys/class/graphics/fbcon/cursor_blink
```

These sysfs writes do **not** persist across reboots — always follow up with
the GRUB change above once you've confirmed the correct rotation value.

Related docs
------------
- `deploy/casedd.service` — disables the blink cursor while CASEDD itself is
  the active console/service (separate from the OS-wide default above).
- `docs/INPUTLESS_MONITOR.md` — CASEDD's own framebuffer claim/unblank
  behavior when no local keyboard/mouse is attached.
- `docs/RESCUE.md` — recovery steps if the display becomes unusable
  (including console/getty recovery).
