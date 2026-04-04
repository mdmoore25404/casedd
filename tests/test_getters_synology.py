"""Tests for :mod:`casedd.getters.synology`."""

from __future__ import annotations

from casedd.data_store import DataStore
from casedd.getters.synology import SynologyGetter


def _route_payload(path: str, query: dict[str, object]) -> dict[str, object]:
    """Return mocked Synology responses by API+method."""
    if path.endswith("/webapi/auth.cgi"):
        return {"success": True, "data": {"sid": "sid-abc"}}

    api = str(query.get("api", ""))
    method = str(query.get("method", ""))
    key = f"{api}.{method}"
    responses: dict[str, dict[str, object]] = {
        "SYNO.Core.System.info": {
            "success": True,
            "data": {
                "hostname": "nas-alpha",
                "model": "DS923+",
                "version_string": "DSM 7.2.1",
            },
        },
        "SYNO.Storage.CGI.Storage.load_info": {
            "success": True,
            "data": {
                "volumes": [
                    {
                        "name": "volume1",
                        "status": "normal",
                        "total_size": 10 * 1024 * 1024 * 1024 * 1024,
                        "used_size": 7 * 1024 * 1024 * 1024 * 1024,
                    }
                ],
                "storage_pools": [{"name": "Pool 1", "status": "normal"}],
                "disks": [
                    {"status": "warning"},
                    {"status": "normal"},
                ],
            },
        },
        "SYNO.Core.Service.list": {
            "success": True,
            "data": {
                "services": [
                    {"display_name": "SMB", "status": "running"},
                    {"display_name": "File Station", "status": "running"},
                    {"display_name": "Synology Drive", "status": "running"},
                    {"display_name": "Surveillance Station", "status": "running"},
                ]
            },
        },
        "SYNO.Core.User.list": {
            "success": True,
            "data": {
                "users": [
                    {"name": "admin"},
                    {"name": "media"},
                ]
            },
        },
        "SYNO.Core.Upgrade.Server.check": {
            "success": True,
            "data": {
                "update": {
                    "available": 1,
                    "version": "DSM 7.2.2",
                }
            },
        },
        "SYNO.SurveillanceStation.Info.getInfo": {
            "success": True,
            "data": {"version": "9.1"},
        },
        "SYNO.SurveillanceStation.Camera.List": {
            "success": True,
            "data": {
                "cameras": [
                    {"id": 1, "name": "Driveway", "status": "recording"},
                    {"id": 2, "name": "Front Door", "status": "online"},
                ]
            },
        },
    }
    return responses.get(key, {"success": True, "data": {}})


async def test_synology_getter_healthy_payload(monkeypatch) -> None:
    """Healthy responses should produce flattened DSM + camera fields."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        interval=1.0,
    )

    monkeypatch.setattr(getter, "_request_json", _route_payload)

    payload = await getter.fetch()

    assert payload["synology.auth.ok"] == 1.0
    assert payload["synology.system.hostname"] == "nas-alpha"
    assert payload["synology.system.model"] == "DS923+"
    assert payload["synology.dsm.update_available"] == 1.0
    assert payload["synology.dsm.latest_version"] == "DSM 7.2.2"
    assert payload["synology.storage.warning_count"] == 1.0
    assert payload["synology.storage.critical_count"] == 0.0
    assert payload["synology.volume_1.name"] == "volume1"
    assert payload["synology.surveillance.available"] == 1.0
    assert payload["synology.surveillance.camera_count"] == 2.0
    assert payload["synology.camera_1.name"] == "Driveway"
    assert "GetSnapshot" in str(payload["synology.camera_1.snapshot_url"])


async def test_synology_getter_auth_failure_returns_placeholder(monkeypatch) -> None:
    """Auth errors should not raise and should emit placeholder values."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="bad",
        interval=1.0,
    )

    def _auth_fail(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": False, "error": {"code": 400}}
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _auth_fail)

    payload = await getter.fetch()

    assert payload["synology.auth.ok"] == 0.0
    assert payload["synology.system.reachable"] == 0.0
    assert payload["synology.system.hostname"] == ""


async def test_synology_getter_applies_volume_and_user_regex(monkeypatch) -> None:
    """Configured regex filters should exclude matching volume and user rows."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        volume_exclude_regex=r"(backup)",
        user_exclude_regex=r"(guest)",
        interval=1.0,
    )

    def _filtered_route(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        key = f"{api}.{method}"
        if key == "SYNO.Storage.CGI.Storage.load_info":
            return {
                "success": True,
                "data": {
                    "volumes": [
                        {"name": "volume1", "total_size": 1, "used_size": 1},
                        {"name": "backup-archive", "total_size": 1, "used_size": 1},
                    ],
                    "disks": [],
                    "storage_pools": [],
                },
            }
        if key == "SYNO.Core.User.list":
            return {
                "success": True,
                "data": {"users": [{"name": "admin"}, {"name": "guest"}]},
            }
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _filtered_route)

    payload = await getter.fetch()

    assert payload["synology.volume.count"] == 1.0
    assert payload["synology.volume_1.name"] == "volume1"
    assert payload["synology.users.count"] == 1.0
    assert payload["synology.user_1.name"] == "admin"


async def test_synology_getter_handles_degraded_and_partial_services(monkeypatch) -> None:
    """Degraded storage and partial service payloads should still normalize."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        interval=1.0,
    )

    def _partial_route(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        key = f"{api}.{method}"
        if key == "SYNO.Storage.CGI.Storage.load_info":
            return {
                "success": True,
                "data": {
                    "disks": [
                        {"status": "critical"},
                        {"status": "degraded"},
                    ]
                },
            }
        if key == "SYNO.Core.Service.list":
            return {
                "success": True,
                "data": {"services": [{"display_name": "SMB", "status": "running"}]},
            }
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _partial_route)

    payload = await getter.fetch()

    assert payload["synology.storage.critical_count"] == 1.0
    assert payload["synology.storage.warning_count"] == 1.0
    assert payload["synology.services.smb_state"] == "running"
    assert payload["synology.services.file_station_state"] == "unknown"
    assert payload["synology.services.synology_drive_state"] == "unknown"
