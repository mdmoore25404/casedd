"""casedd-ctl — lightweight CLI for interacting with a running CASEDD daemon.

Wraps the HTTP API so users and cron jobs can query and control the daemon
without editing config files or using the web viewer.

Usage::

    casedd-ctl status
    casedd-ctl health
    casedd-ctl templates list
    casedd-ctl templates set <name>
    casedd-ctl metrics
    casedd-ctl snapshot [--output path]
    casedd-ctl reload
    casedd-ctl data [--prefix PREFIX]

All commands support ``--json`` for machine-readable output and
``--url`` to target a non-default daemon address.

Public API:
    - :func:`main` — argparse entry point registered as ``casedd-ctl``
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_DEFAULT_URL = os.environ.get("CASEDD_URL", "http://localhost:8080")
_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _request(base_url: str, path: str, method: str = "GET", body: object = None) -> object:
    """Perform a synchronous HTTP request and return the parsed JSON body.

    Args:
        base_url: Daemon base URL (e.g. ``http://localhost:8080``).
        path: API path starting with ``/``.
        method: HTTP method.
        body: Optional JSON-serialisable request body.

    Returns:
        Parsed JSON response.

    Raises:
        SystemExit: On connection error or non-2xx response.
    """
    url = base_url.rstrip("/") + path
    data: bytes | None = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)  # noqa: S310 -- localhost only
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 -- localhost only
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        _die(f"HTTP {exc.code}: {raw}")
    except URLError as exc:
        _die(f"Cannot reach daemon at {url}: {exc.reason}")


def _request_bytes(base_url: str, path: str) -> bytes:
    """Fetch raw bytes from a daemon endpoint.

    Args:
        base_url: Daemon base URL.
        path: API path starting with ``/``.

    Returns:
        Raw response bytes.

    Raises:
        SystemExit: On connection error or non-2xx response.
    """
    url = base_url.rstrip("/") + path
    req = Request(url, method="GET")  # noqa: S310 -- localhost only
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 -- localhost only
            return bytes(resp.read())
    except HTTPError as exc:
        _die(f"HTTP {exc.code}: {exc.reason}")
    except URLError as exc:
        _die(f"Cannot reach daemon at {url}: {exc.reason}")


def _die(msg: str) -> NoReturn:
    """Print an error message and exit with code 1.

    Args:
        msg: Human-readable error description.
    """
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _print_result(data: object, *, as_json: bool) -> None:
    """Print *data* in JSON or a formatted human-readable form.

    Args:
        data: Parsed response payload.
        as_json: When ``True`` output raw JSON.
    """
    if as_json:
        print(json.dumps(data, indent=2))
        return
    if isinstance(data, dict):
        for key, val in data.items():
            print(f"{key}: {val}")
    elif isinstance(data, list):
        for item in data:
            print(item)
    else:
        print(data)


# ---------------------------------------------------------------------------
# Sub-command implementations
# ---------------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> None:
    """Display active template and basic daemon info.

    Args:
        args: Parsed CLI arguments.
    """
    panels_raw = _request(args.url, "/api/panels")
    health_raw = _request(args.url, "/api/health")
    if not isinstance(panels_raw, dict) or not isinstance(health_raw, dict):
        _die("Unexpected response from daemon")
    if args.json:
        print(json.dumps({"panels": panels_raw, "health": health_raw}, indent=2))
        return
    uptime = health_raw.get("uptime_seconds")
    uptime_str = f"{float(uptime):.0f}s" if isinstance(uptime, (int, float)) else "?"
    print(f"status:  {health_raw.get('status', '?')}")
    print(f"uptime:  {uptime_str}")
    panels_list = panels_raw.get("panels", [])
    if isinstance(panels_list, list):
        for panel in panels_list:
            if isinstance(panel, dict):
                print(
                    f"panel '{panel.get('name')}': "
                    f"template={panel.get('current_template', '?')}"
                )


def _cmd_health(args: argparse.Namespace) -> None:
    """Show getter health statuses.

    Args:
        args: Parsed CLI arguments.
    """
    data = _request(args.url, "/api/health")
    if args.json:
        print(json.dumps(data, indent=2))
        return
    if not isinstance(data, dict):
        _die("Unexpected response format")
    print(f"status: {data.get('status', '?')}")
    getters_raw = data.get("getters", [])
    if isinstance(getters_raw, list):
        print(f"\n{'getter':<30} {'status':<10} {'errors':<8}")
        print("-" * 50)
        for g in getters_raw:
            if isinstance(g, dict):
                print(
                    f"{g.get('name', '?')!s:<30} "
                    f"{g.get('status', '?')!s:<10} "
                    f"{g.get('error_count', 0)!s:<8}"
                )


def _cmd_templates_list(args: argparse.Namespace) -> None:
    """List available templates.

    Args:
        args: Parsed CLI arguments.
    """
    data = _request(args.url, "/api/templates")
    if args.json:
        print(json.dumps(data, indent=2))
        return
    if not isinstance(data, dict):
        _die("Unexpected response format")
    templates_list = data.get("templates", [])
    if isinstance(templates_list, list):
        for t in templates_list:
            print(t)


def _cmd_templates_set(args: argparse.Namespace) -> None:
    """Switch active template on the default panel.

    Args:
        args: Parsed CLI arguments.
    """
    # Get default panel name first
    panels_raw = _request(args.url, "/api/panels")
    if not isinstance(panels_raw, dict):
        _die("Unexpected panels response")
    panel: str = args.panel or str(panels_raw.get("default_panel", ""))
    if not panel:
        _die("No panel specified and no default panel found")
    body = {"panel": panel, "template": args.template_name}
    data = _request(args.url, "/api/template/override", method="POST", body=body)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"ok: panel '{panel}' set to template '{args.template_name}'")


def _cmd_metrics(args: argparse.Namespace) -> None:
    """Dump Prometheus-format metrics.

    Args:
        args: Parsed CLI arguments.
    """
    url = args.url.rstrip("/") + "/api/metrics"
    req = Request(url, method="GET")  # noqa: S310 -- localhost only
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 -- localhost only
            print(resp.read().decode())
    except (HTTPError, URLError) as exc:
        _die(f"Cannot reach daemon: {exc}")


def _cmd_snapshot(args: argparse.Namespace) -> None:
    """Save the current rendered frame as a JPEG file.

    Args:
        args: Parsed CLI arguments.
    """
    # Get default panel name
    panels_raw = _request(args.url, "/api/panels")
    panel_name = str(panels_raw.get("default_panel", "")) if isinstance(panels_raw, dict) else ""
    path = args.output or "casedd-snapshot.jpg"
    raw = _request_bytes(args.url, f"/image?panel={panel_name}")
    try:
        with open(path, "wb") as fh:  # noqa: PTH123 -- output path is user-specified  # noqa: PTH123
            fh.write(raw)
    except OSError as exc:
        _die(f"Cannot write snapshot to '{path}': {exc}")
    if not args.json:
        print(f"snapshot saved: {path}")
    else:
        print(json.dumps({"path": path, "bytes": len(raw)}))


def _cmd_data(args: argparse.Namespace) -> None:
    """Dump current data store snapshot.

    Args:
        args: Parsed CLI arguments.
    """
    prefix = getattr(args, "prefix", "") or ""
    path = f"/api/data?prefix={prefix}" if prefix else "/api/data"
    data = _request(args.url, path)
    if args.json:
        print(json.dumps(data, indent=2))
        return
    if isinstance(data, dict):
        store_raw = data.get("data", {})
        if isinstance(store_raw, dict):
            for k, v in sorted(store_raw.items()):
                print(f"{k} = {v}")


def _cmd_reload(args: argparse.Namespace) -> None:
    """Trigger template hot-reload via SIGHUP.

    Args:
        args: Parsed CLI arguments.
    """
    # Read PID file and send SIGHUP.
    pid_file = getattr(args, "pid_file", None) or "run/casedd.pid"
    pid_path = pid_file if isinstance(pid_file, str) else str(pid_file)
    try:
        pid = int(open(pid_path).read().strip())  # noqa: PTH123, SIM115
    except (FileNotFoundError, ValueError):
        _die(f"Cannot read PID from '{pid_path}'. Is the daemon running?")
    try:
        os.kill(pid, signal.SIGHUP)
    except ProcessLookupError:
        _die(f"No process with PID {pid} found.")
    except PermissionError:
        _die(f"No permission to signal PID {pid}.")
    if not args.json:
        print(f"SIGHUP sent to PID {pid}")
    else:
        print(json.dumps({"signal": "SIGHUP", "pid": pid}))


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser.

    Returns:
        Configured :class:`argparse.ArgumentParser`.
    """
    parser = argparse.ArgumentParser(
        prog="casedd-ctl",
        description="Interact with a running CASEDD daemon.",
    )
    parser.add_argument(
        "--url",
        default=_DEFAULT_URL,
        help=f"Daemon base URL (default: {_DEFAULT_URL}, env: CASEDD_URL)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # status
    sub.add_parser("status", help="Show daemon status and active templates")

    # health
    sub.add_parser("health", help="Show getter health statuses")

    # templates
    templates_p = sub.add_parser("templates", help="Template management")
    templates_sub = templates_p.add_subparsers(dest="templates_cmd", metavar="ACTION")
    templates_sub.required = True
    templates_sub.add_parser("list", help="List available templates")
    set_p = templates_sub.add_parser("set", help="Switch active template")
    set_p.add_argument("template_name", help="Template name to activate")
    set_p.add_argument("--panel", default="", help="Panel name (default: first panel)")

    # metrics
    sub.add_parser("metrics", help="Print Prometheus-format metrics")

    # snapshot
    snap_p = sub.add_parser("snapshot", help="Save current frame as JPEG")
    snap_p.add_argument("--output", default="", help="Output path (default: casedd-snapshot.jpg)")

    # data
    data_p = sub.add_parser("data", help="Dump data store snapshot")
    data_p.add_argument("--prefix", default="", help="Filter by key prefix")

    # reload
    reload_p = sub.add_parser("reload", help="Trigger template hot-reload (SIGHUP)")
    reload_p.add_argument(
        "--pid-file",
        default="run/casedd.pid",
        dest="pid_file",
        help="Path to daemon PID file",
    )

    return parser


def main() -> None:
    """Parse arguments and dispatch to the appropriate sub-command.

    This is the entry point registered as ``casedd-ctl`` in pyproject.toml.
    """
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "status":
        _cmd_status(args)
    elif args.command == "health":
        _cmd_health(args)
    elif args.command == "templates":
        if args.templates_cmd == "list":
            _cmd_templates_list(args)
        elif args.templates_cmd == "set":
            _cmd_templates_set(args)
    elif args.command == "metrics":
        _cmd_metrics(args)
    elif args.command == "snapshot":
        _cmd_snapshot(args)
    elif args.command == "data":
        _cmd_data(args)
    elif args.command == "reload":
        _cmd_reload(args)
