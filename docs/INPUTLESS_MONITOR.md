Inputless monitor takeover
=========================

Summary
-------
This project supports an opt-in mode where CASEDD will "claim" the primary
framebuffer at boot when no local keyboard or mouse is attached. When claimed,
CASEDD creates the keep-file `/run/casedd/keep-unblank` so the screen stays
unblanked for CASEDD's frames and external unblanking daemons are not needed.

How it works
------------
- Enable the behaviour by setting the config option `fb_claim_on_no_input` to
  true (YAML) or the environment variable `CASEDD_FB_CLAIM_ON_NO_INPUT=1`.
- At daemon startup CASEDD checks for local input devices. If none are found
  it creates `/run/casedd/keep-unblank` and leaves the display unblanked.
- If a keyboard or mouse is present, CASEDD will not claim the primary
  display (so it won't interfere with local login).

Configuration examples
----------------------
YAML (casedd.yaml):

    fb_claim_on_no_input: true

Environment variable:

    export CASEDD_FB_CLAIM_ON_NO_INPUT=1

Notes and caveats
-----------------
- This is a best-effort heuristic. It uses the `evdev` Python package (if
  installed) to distinguish keyboards/mice from other event devices; otherwise
  it falls back to checking for any `/dev/input/event*` entries.
- Some GPU drivers (notably proprietary NVIDIA) may ignore direct framebuffer
  writes; in those cases CASEDD's renderer or a DRM/KMS-based output may be
  required instead of raw `/dev/fb*` writes.
- The feature is opt-in and guarded by the config flag; it does not change
  behaviour unless explicitly enabled.

Recovery and rescue
-------------------
If you accidentally lock yourself out of the display, common recovery options:

- Remove the keep-file so blanking/unblanking returns to normal (via SSH):

    sudo rm -f /run/casedd/keep-unblank

- Stop CASEDD (if running as a systemd service) to release the framebuffer:

    sudo systemctl stop casedd.service

- If you used the older external unblank daemon and want to restore it:

    sudo systemctl enable --now fb-unblank.service

- If the system does not respond to SSH, use local recovery:
  - Connect a keyboard and press Ctrl+Alt+F2 to switch TTY (if framebuffer not
    overriding the console).
  - Boot a rescue USB or use the host's serial/console if available.
  - Edit GRUB at boot to add `systemd.unit=rescue.target` to get a rescue
    shell.

Troubleshooting
---------------
- If frames are not visible even when claimed:
  - Confirm `/run/casedd/keep-unblank` exists and is owned by root.
  - Check CASEDD logs (or the systemd unit) for renderer errors.
  - If using an NVIDIA driver, try the `fb_test.py` script as root to verify
    writes to `/dev/fb0`:

      sudo env PYTHONPATH=. /home/you/casedd/.venv/bin/python3 scripts/fb_test.py --timeout 30

- To remove CASEDD's claim manually and restore kernel console behavior:

    sudo rm -f /run/casedd/keep-unblank
    echo 0 | sudo tee /sys/class/graphics/fb0/blank

Recommended workflow
--------------------
- Use CASEDD's `fb_claim_on_no_input` for single-monitor embedded use where no
  local keyboard/mouse is expected.
- Keep the older `fb-unblank` service available as a fallback for interactive
  machines where local input may appear intermittently.

Contact
-------
If you need a hand restoring a display or tailoring CASEDD's behaviour to
special hardware (e.g., GPU drivers that manage KMS), open an issue with the
hardware details and I can help adapt the output path.
