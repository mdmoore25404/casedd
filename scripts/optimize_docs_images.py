#!/usr/bin/env python3
"""Incrementally optimize docs images for homepage performance.

This script reads a manifest of source images and generates optimized derivatives:
- Full-size WebP from the original source.
- Thumbnail JPEG for broad compatibility.
- Thumbnail WebP for modern browsers.

To avoid unnecessary rebuilds, it stores source signatures in
``docs/.jekyll-cache/image_optimization_state.json`` and only regenerates when a
source file changes or derivative files are missing.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class OptimizationTarget:
    """Configuration for one source image optimization target.

    Attributes:
        source: Source file path relative to ``docs/images``.
        thumb_max_width: Maximum thumbnail width in pixels.
        thumb_max_height: Maximum thumbnail height in pixels.
        jpeg_quality: JPEG quality for thumbnail output.
        webp_quality: WebP quality for full and thumbnail outputs.
    """

    source: str
    thumb_max_width: int
    thumb_max_height: int
    jpeg_quality: int
    webp_quality: int


def _load_manifest(manifest_path: Path) -> list[OptimizationTarget]:
    """Load optimization targets from the JSON manifest.

    Args:
        manifest_path: Absolute path to optimization manifest JSON.

    Returns:
        List of optimization targets.

    Raises:
        FileNotFoundError: If the manifest does not exist.
        ValueError: If manifest structure is invalid.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing optimization manifest: {manifest_path}")

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list):
        raise ValueError("optimization manifest must define a 'targets' array")

    targets: list[OptimizationTarget] = []
    for raw in raw_targets:
        if not isinstance(raw, dict):
            raise ValueError("each optimization target must be an object")

        source = raw.get("source")
        if not isinstance(source, str) or source.strip() == "":
            raise ValueError("each optimization target must define non-empty 'source'")

        target = OptimizationTarget(
            source=source,
            thumb_max_width=int(raw.get("thumb_max_width", 560)),
            thumb_max_height=int(raw.get("thumb_max_height", 560)),
            jpeg_quality=int(raw.get("jpeg_quality", 72)),
            webp_quality=int(raw.get("webp_quality", 76)),
        )
        targets.append(target)

    return targets


def _load_state(state_path: Path) -> dict[str, str]:
    """Load prior source signatures.

    Args:
        state_path: Absolute path to cache state JSON.

    Returns:
        Mapping of source image path to file signature.
    """
    if not state_path.exists():
        return {}

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    state = payload.get("sources")
    if not isinstance(state, dict):
        return {}

    return {str(k): str(v) for k, v in state.items()}


def _save_state(state_path: Path, state: dict[str, str]) -> None:
    """Persist source signatures for incremental optimization.

    Args:
        state_path: Absolute path to cache state JSON.
        state: Source-path-to-signature mapping.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sources": state}
    state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _signature(path: Path) -> str:
    """Build a stable source signature from stat metadata.

    Args:
        path: Path to source image.

    Returns:
        Signature string encoding mtime and size.
    """
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _derive_output_paths(source_path: Path) -> tuple[Path, Path, Path]:
    """Build output paths for full WebP and thumbnail variants.

    Args:
        source_path: Absolute path to source image.

    Returns:
        Tuple of ``(full_webp, thumb_jpg, thumb_webp)`` paths.
    """
    stem = source_path.stem
    parent = source_path.parent
    full_webp = parent / f"{stem}.webp"
    thumb_jpg = parent / f"{stem}-thumb.jpg"
    thumb_webp = parent / f"{stem}-thumb.webp"
    return full_webp, thumb_jpg, thumb_webp


def _build_derivatives(source_path: Path, target: OptimizationTarget) -> None:
    """Create optimized derivatives for one source image.

    Args:
        source_path: Absolute source image path.
        target: Optimization parameters.
    """
    full_webp_path, thumb_jpg_path, thumb_webp_path = _derive_output_paths(source_path)

    with Image.open(source_path) as image:
        webp_image = image.convert("RGBA") if image.mode in {"RGBA", "LA"} else image.convert("RGB")
        webp_image.save(
            full_webp_path,
            format="WEBP",
            quality=target.webp_quality,
            method=6,
        )

        thumb = image.convert("RGB")
        thumb.thumbnail(
            (target.thumb_max_width, target.thumb_max_height),
            Image.Resampling.LANCZOS,
        )
        thumb.save(
            thumb_jpg_path,
            format="JPEG",
            quality=target.jpeg_quality,
            optimize=True,
            progressive=True,
        )
        thumb.save(
            thumb_webp_path,
            format="WEBP",
            quality=target.webp_quality,
            method=6,
        )


def main() -> int:
    """Run incremental image optimization for docs assets.

    Returns:
        Process exit code where ``0`` indicates success.
    """
    repo_root = Path(__file__).resolve().parent.parent
    docs_images_dir = repo_root / "docs" / "images"
    manifest_path = docs_images_dir / "optimization_manifest.json"
    state_path = repo_root / "docs" / ".jekyll-cache" / "image_optimization_state.json"

    targets = _load_manifest(manifest_path)
    previous_state = _load_state(state_path)
    next_state = dict(previous_state)

    rebuilt_count = 0
    skipped_count = 0
    for target in targets:
        source_path = docs_images_dir / target.source
        if not source_path.exists():
            raise FileNotFoundError(f"Missing optimization source image: {source_path}")

        sig = _signature(source_path)
        full_webp_path, thumb_jpg_path, thumb_webp_path = _derive_output_paths(source_path)
        outputs_exist = full_webp_path.exists() and thumb_jpg_path.exists() and thumb_webp_path.exists()

        if previous_state.get(target.source) == sig and outputs_exist:
            skipped_count += 1
            continue

        _build_derivatives(source_path, target)
        next_state[target.source] = sig
        rebuilt_count += 1

    _save_state(state_path, next_state)
    print(f"optimized images: rebuilt={rebuilt_count} skipped={skipped_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
