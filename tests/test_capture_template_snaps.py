"""Tests for the developer template snapshot capture script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "capture_template_snaps.py"
_SPEC = importlib.util.spec_from_file_location("capture_template_snaps", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - import bootstrap guard
    raise RuntimeError("Could not load capture_template_snaps.py")
capture_template_snaps = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = capture_template_snaps
_SPEC.loader.exec_module(capture_template_snaps)


def test_write_manifest_uses_existing_png_files(tmp_path: Path) -> None:
    """Manifest refresh without explicit templates should mirror on-disk PNG files."""
    (tmp_path / "system_stats.png").write_bytes(b"png")
    (tmp_path / "weather_nws.png").write_bytes(b"png")

    capture_template_snaps._write_manifest(tmp_path)

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in payload["templates"]] == [
        "system_stats",
        "weather_nws",
    ]


def test_write_manifest_excludes_private_patterns_unless_demo(tmp_path: Path) -> None:
    """Manifest inclusion should follow .gitignore patterns with negation overrides."""
    (tmp_path / ".gitignore").write_text(
        "*nzbget*.png\n"
        "*servarr*.png\n"
        "!*_demo*.png\n",
        encoding="utf-8",
    )
    (tmp_path / "system_stats.png").write_bytes(b"png")
    (tmp_path / "nzbget_dashboard.png").write_bytes(b"png")
    (tmp_path / "servarr_dashboard_demo.png").write_bytes(b"png")

    capture_template_snaps._write_manifest(tmp_path)

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in payload["templates"]] == [
        "servarr_dashboard_demo",
        "system_stats",
    ]


def test_write_manifest_respects_custom_snapshot_gitignore_rules(tmp_path: Path) -> None:
    """Custom .gitignore entries should directly control manifest inclusion."""
    (tmp_path / ".gitignore").write_text(
        "*apod*.png\n"
        "!apod_demo.png\n",
        encoding="utf-8",
    )
    (tmp_path / "apod.png").write_bytes(b"png")
    (tmp_path / "apod_demo.png").write_bytes(b"png")
    (tmp_path / "system_stats.png").write_bytes(b"png")

    capture_template_snaps._write_manifest(tmp_path)

    payload = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in payload["templates"]] == [
        "apod_demo",
        "system_stats",
    ]


def test_prompt_confirmation_supports_skip_and_approve_all(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    """Interactive approval should allow skip and bulk-approve behavior."""
    answers = iter(("s", "a"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    approved = capture_template_snaps._prompt_confirmation(
        ("system_stats", "weather_nws", "docker_dashboard"),
        tmp_path,
    )

    assert approved == ("weather_nws", "docker_dashboard")


def test_wait_for_template_data_polls_until_required_prefixes_present(monkeypatch: object) -> None:
    """Explicit templates should wait for getter-backed data to appear before capture."""
    payloads = iter(
        (
            {"data": {"cpu.percent": 12.0}},
            {"data": {"cpu.percent": 12.0, "sysinfo.rows": "ready"}},
        )
    )
    monkeypatch.setattr(
        capture_template_snaps,
        "_template_source_prefixes",
        lambda template_name: ("cpu.", "sysinfo."),
    )
    monkeypatch.setattr(
        capture_template_snaps,
        "_request_json",
        lambda base_url, path, method="GET", body=None: next(payloads),
    )
    monkeypatch.setattr(
        capture_template_snaps.time,
        "sleep",
        lambda seconds: None,
    )

    capture_template_snaps._wait_for_template_data(
        "http://localhost:8080",
        "sysinfo",
        timeout_seconds=1.0,
    )
