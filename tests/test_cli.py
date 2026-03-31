"""Tests for :mod:`casedd.cli` — casedd-ctl CLI (issue #51)."""

from __future__ import annotations

import json
from pathlib import Path
import signal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from casedd import cli

# ---------------------------------------------------------------------------
# _request helpers (unit-tested via mock)
# ---------------------------------------------------------------------------


def _mock_urlopen(payload: Any) -> Any:
    """Return a context manager mock that yields a fake HTTP response."""
    body = json.dumps(payload).encode()
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    cm = MagicMock()
    cm.__enter__ = lambda s: resp
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def test_parser_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    """Calling the parser with no args exits with an error."""
    parser = cli._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_parser_status() -> None:
    """Parser correctly identifies 'status' command."""
    parser = cli._build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_parser_health() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["health"])
    assert args.command == "health"


def test_parser_help_root() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["help"])
    assert args.command == "help"
    assert args.topics == []


def test_parser_help_templates_set() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["help", "templates", "set"])
    assert args.command == "help"
    assert args.topics == ["templates", "set"]


def test_parser_templates_list() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["templates", "list"])
    assert args.command == "templates"
    assert args.templates_cmd == "list"


def test_parser_templates_set() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["templates", "set", "mytemplate"])
    assert args.command == "templates"
    assert args.templates_cmd == "set"
    assert args.template_name == "mytemplate"


def test_parser_template_alias_set() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["template", "set", "mytemplate"])
    assert args.command == "template"
    assert args.templates_cmd == "set"
    assert args.template_name == "mytemplate"


def test_parser_metrics() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["metrics"])
    assert args.command == "metrics"


def test_parser_snapshot_default_output() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["snapshot"])
    assert args.command == "snapshot"
    assert args.output == ""


def test_parser_snapshot_custom_output() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["snapshot", "--output", "/tmp/frame.jpg"])
    assert args.output == "/tmp/frame.jpg"


def test_parser_data_prefix() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["data", "--prefix", "cpu"])
    assert args.prefix == "cpu"


def test_parser_json_flag() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["--json", "health"])
    assert args.json is True


def test_parser_custom_url() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(["--url", "http://192.168.1.50:8080", "status"])
    assert args.url == "http://192.168.1.50:8080"


# ---------------------------------------------------------------------------
# _print_result
# ---------------------------------------------------------------------------


def test_print_result_json_mode(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_result({"a": 1, "b": "x"}, as_json=True)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == {"a": 1, "b": "x"}


def test_print_result_human_dict(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_result({"foo": "bar"}, as_json=False)
    out = capsys.readouterr().out
    assert "foo" in out
    assert "bar" in out


def test_print_result_human_list(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_result(["alpha", "beta"], as_json=False)
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "beta" in out


# ---------------------------------------------------------------------------
# _die
# ---------------------------------------------------------------------------


def test_die_exits_with_1(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli._die("something went wrong")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "something went wrong" in err


# ---------------------------------------------------------------------------
# _cmd_health (integration via mock)
# ---------------------------------------------------------------------------


def test_cmd_health_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    health_resp = {
        "status": "ok",
        "uptime_seconds": 120.0,
        "render_count": 50,
        "getters": [
            {"name": "CpuGetter", "status": "ok", "error_count": 0}
        ],
    }
    args = cli._build_parser().parse_args(["health"])
    with patch("casedd.cli.urlopen", return_value=_mock_urlopen(health_resp)):
        cli._cmd_health(args)
    out = capsys.readouterr().out
    assert "ok" in out
    assert "CpuGetter" in out


def test_cmd_health_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    health_resp = {
        "status": "degraded",
        "uptime_seconds": 5.0,
        "render_count": 1,
        "getters": [],
    }
    args = cli._build_parser().parse_args(["--json", "health"])
    with patch("casedd.cli.urlopen", return_value=_mock_urlopen(health_resp)):
        cli._cmd_health(args)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["status"] == "degraded"


# ---------------------------------------------------------------------------
# _cmd_templates_list
# ---------------------------------------------------------------------------


def test_cmd_templates_list(capsys: pytest.CaptureFixture[str]) -> None:
    resp = {"templates": ["system_stats", "htop", "fans"]}
    args = cli._build_parser().parse_args(["templates", "list"])
    with patch("casedd.cli.urlopen", return_value=_mock_urlopen(resp)):
        cli._cmd_templates_list(args)
    out = capsys.readouterr().out
    assert "system_stats" in out
    assert "htop" in out


# ---------------------------------------------------------------------------
# _cmd_data
# ---------------------------------------------------------------------------


def test_cmd_data_human_output(capsys: pytest.CaptureFixture[str]) -> None:
    resp = {"data": {"cpu.percent": 45.2, "memory.used_gb": 8.1}}
    args = cli._build_parser().parse_args(["data"])
    with patch("casedd.cli.urlopen", return_value=_mock_urlopen(resp)):
        cli._cmd_data(args)
    out = capsys.readouterr().out
    assert "cpu.percent" in out
    assert "45.2" in out


# ---------------------------------------------------------------------------
# _cmd_reload
# ---------------------------------------------------------------------------


def test_cmd_reload_sends_sighup(tmp_path: Path) -> None:
    pid_file = tmp_path / "casedd.pid"
    pid_file.write_text("12345\n")
    args = cli._build_parser().parse_args(["reload", "--pid-file", str(pid_file)])
    with patch("os.kill") as mock_kill:
        cli._cmd_reload(args)
        mock_kill.assert_called_once_with(12345, signal.SIGHUP)


def test_cmd_reload_missing_pid_file_exits(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._build_parser().parse_args(
        ["reload", "--pid-file", "/nonexistent/path/casedd.pid"]
    )
    with pytest.raises(SystemExit) as exc:
        cli._cmd_reload(args)
    assert exc.value.code == 1


def test_cmd_help_templates_output(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._build_parser().parse_args(["help", "templates"])
    cli._cmd_help(args)
    out = capsys.readouterr().out
    assert "usage: casedd-ctl templates" in out
    assert "list" in out
    assert "set" in out


def test_cmd_help_template_alias_output(capsys: pytest.CaptureFixture[str]) -> None:
    args = cli._build_parser().parse_args(["help", "template", "set"])
    cli._cmd_help(args)
    out = capsys.readouterr().out
    assert "template_name" in out
    assert "--panel" in out


def test_cmd_help_unknown_topic_exits() -> None:
    args = cli._build_parser().parse_args(["help", "not-a-command"])
    with pytest.raises(SystemExit) as exc:
        cli._cmd_help(args)
    assert exc.value.code == 1
