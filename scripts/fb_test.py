#!/usr/bin/env python3
"""Probe /dev/fb0 and write several test frames.

Usage: python3 scripts/fb_test.py

This script prints framebuffer info and attempts to write a short sequence
of solid and patterned images to the device. Running user needs permission
to write /dev/fb0 (video group or root).
"""
from __future__ import annotations

import argparse
import sys
import time
import random
from pathlib import Path
from PIL import Image, ImageDraw

from casedd.usb_display import probe_framebuffer
from casedd.outputs.framebuffer import FramebufferOutput

DEV = Path("/dev/fb0")


def make_checker(w: int, h: int, block: int = 32) -> Image.Image:
    img = Image.new("RGB", (w, h), "black")
    draw = ImageDraw.Draw(img)
    for y in range(0, h, block):
        for x in range(0, w, block):
            color = (255, 255, 255) if ((x // block) + (y // block)) % 2 == 0 else (0, 0, 0)
            draw.rectangle([x, y, x + block - 1, y + block - 1], fill=color)
    return img


def make_gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    for x in range(w):
        for y in range(h):
            img.putpixel((x, y), (int(255 * x / max(1, w - 1)), int(255 * y / max(1, h - 1)), 128))
    return img


def make_random(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    pixels = img.load()
    for y in range(h):
        for x in range(w):
            pixels[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    return img


def run_loop(fb: FramebufferOutput, patterns: list[tuple[str, Image.Image]], dwell: float, timeout: float | None) -> None:
    start = time.time()
    idx = 0
    try:
        while True:
            if timeout is not None and (time.time() - start) >= timeout:
                break
            name, img = patterns[idx % len(patterns)]
            print("Displaying:", name)
            fb.write(img)
            slept = 0.0
            while slept < dwell:
                time.sleep(min(0.5, dwell - slept))
                slept += min(0.5, dwell - slept)
                if timeout is not None and (time.time() - start) >= timeout:
                    return
            idx += 1
    except KeyboardInterrupt:
        print("Interrupted by user")


def main() -> int:
    p = argparse.ArgumentParser(description="Framebuffer test pattern rotator")
    p.add_argument("--timeout", type=float, default=0.0, help="Seconds to run (0 = until Ctrl-C)")
    p.add_argument("--dwell", type=float, default=1.0, help="Seconds per pattern")
    args = p.parse_args()

    timeout = None if args.timeout <= 0.0 else args.timeout

    print("Probing", DEV)
    info = probe_framebuffer(DEV)
    if info is None:
        print("No framebuffer info available for", DEV)
        return 2

    print("Framebuffer info:")
    print("  device:", info.device)
    print("  resolution:", info.width, "x", info.height)
    print("  bpp:", info.bpp)
    print("  is_usb:", info.is_usb)
    print("  usb_vendor:product:", f"{info.usb_vendor_id}:{info.usb_product_id}" if info.usb_vendor_id else None)
    print("  driver:", info.driver)
    print("  supported_modes:", info.supported_modes)
    print("  supported_hz:", info.supported_hz)

    fb = FramebufferOutput(DEV, disabled=False)
    if not getattr(fb, "_enabled", False):
        print("Framebuffer output not enabled (check permissions or device). Exiting.")
        return 3

    w = info.width or 800
    h = info.height or 480
    print(f"Using target resolution {w}x{h} for test frames")

    patterns = [
        ("red", Image.new("RGB", (w, h), (255, 0, 0))),
        ("green", Image.new("RGB", (w, h), (0, 255, 0))),
        ("blue", Image.new("RGB", (w, h), (0, 0, 255))),
        ("checker", make_checker(w, h, block=max(8, min(w, h) // 20))),
        ("gradient", make_gradient(w, h)),
        ("random", make_random(w, h)),
    ]

    run_loop(fb, patterns, args.dwell, timeout)

    print("Test finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
