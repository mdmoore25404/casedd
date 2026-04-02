#!/usr/bin/env python3
"""Capture template snapshots for the docs gallery.

This developer-only script talks to a running CASEDD daemon, forces one
template at a time on the target panel, captures the rendered frame, and
stores PNG files under ``docs/images/template_snaps``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import fnmatch
from io import BytesIO
import json
from pathlib import Path
import time
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

from casedd.template.loader import load_template
from casedd.template.models import Template, WidgetConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "http://localhost:8080"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "images" / "template_snaps"
DEFAULT_FIXTURE_DIR = REPO_ROOT / "scripts" / "fixtures"
DEFAULT_DATA_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class _PanelInfo:
    """Minimal panel metadata required for snapshot capture."""

    name: str
    base_template: str
    rotation_templates: tuple[str, ...]
    forced_template: str


def _request_json(base_url: str, path: str, method: str = "GET", body: object = None) -> object:
    """Perform an HTTP request and decode a JSON payload."""
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read()
    except HTTPError as exc:  # pragma: no cover - exercised manually
        raise SystemExit(f"HTTP {exc.code} while requesting {url}") from exc
    except URLError as exc:  # pragma: no cover - exercised manually
        raise SystemExit(f"Could not reach {url}: {exc.reason}") from exc
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _request_bytes(base_url: str, path: str) -> bytes:
    """Fetch raw bytes from the daemon."""
    url = base_url.rstrip("/") + path
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            return bytes(response.read())
    except HTTPError as exc:  # pragma: no cover - exercised manually
        raise SystemExit(f"HTTP {exc.code} while requesting {url}") from exc
    except URLError as exc:  # pragma: no cover - exercised manually
        raise SystemExit(f"Could not reach {url}: {exc.reason}") from exc


def _panel_state(base_url: str, panel_name: str | None) -> tuple[str, _PanelInfo, tuple[str, ...]]:
    """Fetch current panel metadata and available template names."""
    payload = _request_json(base_url, "/api/panels")
    if not isinstance(payload, dict):
        raise SystemExit("Unexpected /api/panels response")
    default_panel = str(payload.get("default_panel") or "")
    selected_panel = panel_name or default_panel
    panels_obj = payload.get("panels")
    if not isinstance(panels_obj, list):
        raise SystemExit("Unexpected panel list in /api/panels response")

    panel_info: _PanelInfo | None = None
    for panel_obj in panels_obj:
        if not isinstance(panel_obj, dict):
            continue
        if str(panel_obj.get("name") or "") != selected_panel:
            continue
        panel_info = _PanelInfo(
            name=selected_panel,
            base_template=str(panel_obj.get("base_template") or ""),
            rotation_templates=tuple(
                str(item)
                for item in cast("Sequence[object]", panel_obj.get("rotation_templates") or [])
                if isinstance(item, str) and item
            ),
            forced_template=str(panel_obj.get("forced_template") or ""),
        )
        break
    if panel_info is None:
        raise SystemExit(f"Unknown panel '{selected_panel}'")

    templates_payload = _request_json(base_url, "/api/templates")
    if not isinstance(templates_payload, dict):
        raise SystemExit("Unexpected /api/templates response")
    templates_obj = templates_payload.get("templates")
    if not isinstance(templates_obj, list):
        raise SystemExit("Unexpected template list in /api/templates response")
    templates = tuple(str(item) for item in templates_obj if isinstance(item, str) and item)
    return selected_panel, panel_info, templates


def _determine_targets(
    mode_all: bool,
    mode_active: bool,
    template_name: str | None,
    panel_info: _PanelInfo,
    available_templates: Sequence[str],
) -> tuple[str, ...]:
    """Resolve which templates should be captured."""
    if template_name is not None:
        if template_name not in available_templates:
            raise SystemExit(f"Unknown template '{template_name}'")
        return (template_name,)
    if mode_all:
        return tuple(sorted(set(available_templates)))
    if mode_active:
        ordered = [panel_info.base_template, *panel_info.rotation_templates]
        return tuple(dict.fromkeys(name for name in ordered if name))
    raise SystemExit("Choose one of --all, --active, or --template")


def _set_template_override(base_url: str, panel_name: str, template_name: str) -> None:
    """Force one template on the target panel."""
    _request_json(
        base_url,
        "/api/template/override",
        method="POST",
        body={"panel": panel_name, "template": template_name},
    )


def _wait_for_template(base_url: str, panel_name: str, template_name: str, timeout_seconds: float) -> None:
    """Wait until the panel reports the selected template as current."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        payload = _request_json(base_url, "/api/panels")
        if not isinstance(payload, dict):
            break
        panels_obj = payload.get("panels")
        if not isinstance(panels_obj, list):
            break
        for panel_obj in panels_obj:
            if not isinstance(panel_obj, dict):
                continue
            if str(panel_obj.get("name") or "") != panel_name:
                continue
            if str(panel_obj.get("current_template") or "") == template_name:
                return
        time.sleep(0.25)
    raise SystemExit(f"Timed out waiting for panel '{panel_name}' to switch to '{template_name}'")


def _capture_image(base_url: str, panel_name: str, output_path: Path) -> None:
    """Capture the current panel image and save it as PNG."""
    raw = _request_bytes(base_url, f"/image?panel={panel_name}")
    image = Image.open(BytesIO(raw)).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")


def _write_manifest(output_dir: Path, templates: Sequence[str] | None = None) -> None:
    """Write the gallery manifest consumed by the landing page."""
    rules = _load_snapshot_gitignore_rules(output_dir)
    if templates is not None:
        manifest_templates = [
            template_name
            for template_name in templates
            if _should_include_manifest_image(f"{template_name}.png", rules)
        ]
    else:
        manifest_templates = [
            path.stem
            for path in sorted(output_dir.glob("*.png"))
            if _should_include_manifest_image(path.name, rules)
        ]
    manifest = {
        "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "templates": [
            {
                # Strip _demo suffix so gallery shows the canonical template name.
                "name": template_name.removesuffix("_demo"),
                "image": f"images/template_snaps/{template_name}.png",
            }
            for template_name in manifest_templates
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_snapshot_gitignore_rules(output_dir: Path) -> tuple[tuple[str, bool], ...]:
    """Load gitignore-style include/exclude rules from the snapshot output directory."""
    gitignore_path = output_dir / ".gitignore"
    if not gitignore_path.exists():
        return ()

    rules: list[tuple[str, bool]] = []
    for line in gitignore_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        is_negated = stripped.startswith("!")
        pattern = stripped[1:] if is_negated else stripped
        pattern = pattern.strip()
        if not pattern or pattern.endswith("/"):
            continue
        rules.append((pattern, is_negated))

    return tuple(rules)


def _should_include_manifest_image(file_name: str, rules: Sequence[tuple[str, bool]]) -> bool:
    """Apply ordered gitignore-style rules to decide if one snapshot image is includable."""
    included = True
    for pattern, is_negated in rules:
        if fnmatch.fnmatch(file_name, pattern):
            included = is_negated
    return included


def _parse_args() -> argparse.Namespace:
    """Build and parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Capture CASEDD template snapshots for docs")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", dest="all_templates")
    mode.add_argument("--active", action="store_true", dest="active_templates")
    mode.add_argument("--template", default=None, help="Capture one template by name")
    parser.add_argument("--url", default=DEFAULT_URL, help="CASEDD base URL")
    parser.add_argument("--panel", default="", help="Panel name to capture")
    parser.add_argument(
        "--settle-seconds",
        default=2.5,
        type=float,
        help="Seconds to wait after a template switch before capture",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to write PNGs and manifest.json",
    )
    parser.add_argument(
        "--data-timeout-seconds",
        default=DEFAULT_DATA_TIMEOUT_SECONDS,
        type=float,
        help="Seconds to wait for template source data to appear before capture",
    )
    parser.add_argument(
        "--fixture-dir",
        default=str(DEFAULT_FIXTURE_DIR),
        help=(
            "Directory containing fixture JSON files named {template}.json. "
            "When a matching fixture is found the data is pushed via /api/update "
            "before capture and the output is saved as {template}_demo.png."
        ),
    )
    return parser.parse_args()


def _fixture_path(fixture_dir: Path, template_name: str) -> Path | None:
    """Return the fixture JSON path for a template, or None if absent."""
    candidate = fixture_dir / f"{template_name}.json"
    return candidate if candidate.is_file() else None


def _push_fixture_data(base_url: str, fixture_path: Path) -> None:
    """Push all update records from a fixture file via /api/update.

    Reads a replay-format fixture file and POSTs each record's ``update``
    payload to the daemon's REST ingestion endpoint, making the data
    immediately available in the store before the snapshot is captured.

    Args:
        base_url: CASEDD daemon base URL.
        fixture_path: Path to the fixture JSON file.
    """
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    records = raw.get("records", [])
    if not records:
        return
    for record in records:
        update = record.get("update")
        if isinstance(update, dict) and update:
            _request_json(base_url, "/api/update", method="POST", body={"update": update})


def _collect_widget_sources(cfg: WidgetConfig, out: set[str]) -> None:
    """Collect nested source references from one widget config tree."""
    if cfg.source:
        out.add(cfg.source)
    for source in cfg.sources:
        out.add(source)
    for child in cfg.children:
        _collect_widget_sources(child, out)
    for child in cfg.children_named.values():
        _collect_widget_sources(child, out)


def _template_sources(template: Template) -> set[str]:
    """Collect all source references used by a template."""
    sources: set[str] = set()
    for widget in template.widgets.values():
        _collect_widget_sources(widget, sources)
    return sources


def _template_source_prefixes(template_name: str) -> tuple[str, ...]:
    """Return getter namespace prefixes referenced by a template."""
    template = load_template(REPO_ROOT / "templates" / f"{template_name}.casedd")
    prefixes = {
        f"{source.split('.', maxsplit=1)[0]}."
        for source in _template_sources(template)
        if "." in source
    }
    return tuple(sorted(prefixes))


def _wait_for_template_data(
    base_url: str,
    template_name: str,
    timeout_seconds: float,
) -> None:
    """Wait for at least one live store key for each referenced source namespace."""
    prefixes = _template_source_prefixes(template_name)
    if not prefixes:
        return

    deadline = time.monotonic() + timeout_seconds
    remaining = set(prefixes)
    while time.monotonic() < deadline:
        payload = _request_json(base_url, "/api/data")
        if not isinstance(payload, dict):
            break
        data_obj = payload.get("data")
        if not isinstance(data_obj, dict):
            break

        present_prefixes = {
            prefix
            for prefix in prefixes
            if any(isinstance(key, str) and key.startswith(prefix) for key in data_obj)
        }
        remaining = set(prefixes) - present_prefixes
        if not remaining:
            return
        time.sleep(0.5)

    missing = ", ".join(sorted(remaining))
    raise SystemExit(
        f"Timed out waiting for data for template '{template_name}' (missing namespaces: {missing})"
    )


def _prompt_confirmation(targets: Sequence[str], output_dir: Path) -> tuple[str, ...]:
    """Prompt the user to approve, skip, or bulk-approve planned captures."""
    print("Planned template captures:")
    for template_name in targets:
        output_path = output_dir / f"{template_name}.png"
        action = "replace" if output_path.exists() else "create"
        print(f"  - {template_name}: {action} {output_path.name}")

    approved: list[str] = []
    approve_all_remaining = False
    for template_name in targets:
        if approve_all_remaining:
            approved.append(template_name)
            continue

        while True:
            choice = input(
                f"Capture {template_name}? [y] approve / [a] approve all remaining / [s] skip: "
            ).strip().lower()
            if choice in {"y", "yes"}:
                approved.append(template_name)
                break
            if choice in {"a", "all"}:
                approved.append(template_name)
                approve_all_remaining = True
                break
            if choice in {"s", "skip"}:
                break
            print("Enter 'y', 'a', or 's'.")

    return tuple(approved)


def main() -> None:
    """Capture the requested template snapshots and restore the prior override."""
    args = _parse_args()
    output_dir = Path(args.output_dir)
    panel_name, panel_info, available_templates = _panel_state(
        args.url,
        args.panel or None,
    )
    targets = _determine_targets(
        args.all_templates,
        args.active_templates,
        cast("str | None", args.template),
        panel_info,
        available_templates,
    )
    approved_targets = _prompt_confirmation(targets, output_dir)
    if not approved_targets:
        _write_manifest(output_dir)
        print("No snapshots approved; manifest refreshed from existing PNG files only.")
        return

    original_override = panel_info.forced_template or "auto"
    fixture_dir = Path(args.fixture_dir)
    print(f"Capturing {len(approved_targets)} template snapshot(s) for panel '{panel_name}'...")

    try:
        for template_name in approved_targets:
            fix = _fixture_path(fixture_dir, template_name)
            # Fixture-based captures use a _demo suffix so they pass the
            # .gitignore negation rule and can be committed safely.
            output_name = f"{template_name}_demo.png" if fix else f"{template_name}.png"
            print(f"  - {template_name}{' (fixture)' if fix else ''}")
            _set_template_override(args.url, panel_name, template_name)
            _wait_for_template(
                args.url,
                panel_name,
                template_name,
                max(5.0, args.settle_seconds + 2.0),
            )
            if fix:
                _push_fixture_data(args.url, fix)
            _wait_for_template_data(
                args.url,
                template_name,
                max(args.data_timeout_seconds, args.settle_seconds + 2.0),
            )
            time.sleep(max(0.1, float(args.settle_seconds)))
            _capture_image(args.url, panel_name, output_dir / output_name)
    finally:
        _set_template_override(args.url, panel_name, original_override)

    _write_manifest(output_dir)
    print(f"Wrote snapshots and manifest to {output_dir}")


if __name__ == "__main__":
    main()
