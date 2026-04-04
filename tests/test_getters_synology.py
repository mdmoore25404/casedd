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
        "SYNO.Core.Package.list": {
            "success": True,
            "data": {
                "packages": [
                    {"id": "HyperBackup", "status": "running"},
                ]
            },
        },
        "SYNO.Backup.Task.list": {
            "success": True,
            "data": {
                "tasks": [
                    {"name": "nas1 backup", "status": "success"},
                    {"name": "usb backup", "status": "failed"},
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
        "SYNO.Core.Share.list": {
            "success": True,
            "data": {
                "shares": [
                    {"name": "plexmedia", "vol_path": "/volume2"},
                    {"name": "k8s", "vol_path": "/volume2"},
                ]
            },
        },
        "SYNO.Core.System.Utilization.get": {
            "success": True,
            "data": {
                "cpu": {"user_load": 10, "system_load": 5, "other_load": 2},
                "memory": {"real_usage": 44},
                "network": [{"device": "total", "rx": 4096, "tx": 8192}],
                "disk": {
                    "disk": [
                        {"read_byte": 1048576, "write_byte": 2097152},
                    ]
                },
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
    assert payload["synology.performance.cpu_percent"] == 12.0
    assert payload["synology.performance.ram_percent"] == 44.0
    assert payload["synology.performance.net_rx_kbps"] == 4.0
    assert payload["synology.performance.net_tx_kbps"] == 8.0
    assert payload["synology.performance.disk_read_mb_s"] == 1.0
    assert payload["synology.performance.disk_write_mb_s"] == 2.0
    assert payload["synology.storage.warning_count"] == 1.0
    assert payload["synology.storage.critical_count"] == 0.0
    assert payload["synology.volume_1.name"] == "volume1"
    assert "Disk|WARN|" in str(payload["synology.disks.rows"])
    assert "plexmedia|volume2" in str(payload["synology.shares.rows"])
    assert "DSM Update|exited|AVAILABLE" in str(payload["synology.status.rows"])
    assert "Driveway" not in str(payload["synology.status.rows"])
    assert "Driveway" in str(payload["synology.surveillance.status.rows"])
    assert payload["synology.surveillance.available"] == 1.0
    assert payload["synology.surveillance.camera_count"] == 2.0
    assert payload["synology.camera_1.name"] == "Driveway"
    assert "GetSnapshot" in str(payload["synology.camera_1.snapshot_url"])
    assert payload["synology.backup.installed"] == 1.0
    assert payload["synology.backup.configured"] == 1.0
    assert payload["synology.backup.success"] == 0.0
    assert payload["synology.backup.summary"] == "1/2 ok, 1 failed"
    assert "Backups|exited|1/2 ok, 1 failed" in str(payload["synology.status.rows"])
    assert "nas1 backup|started|SUCCESS" in str(payload["synology.backup.rows"])
    assert "usb backup|exited|FAILURE" in str(payload["synology.backup.rows"])
    assert "nas1 backup|started|SUCCESS" in str(payload["synology.status.rows"])


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
    assert payload["synology.services.synology_drive_state"] == "not installed"


async def test_synology_getter_prefers_online_cameras_for_snapshots(monkeypatch) -> None:
    """Camera list should prioritize online/recording rows over offline entries."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        camera_include_regex=r"(Drive|Garage)",
        interval=1.0,
    )

    def _camera_route(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        key = f"{api}.{method}"
        if key == "SYNO.SurveillanceStation.Info.getInfo":
            return {"success": True, "data": {"version": "9.1"}}
        if key == "SYNO.SurveillanceStation.Camera.List":
            return {
                "success": True,
                "data": {
                    "cameras": [
                        {"id": 1, "name": "Driveway", "status": 1},
                        {"id": 3, "name": "Old Cam", "status": 7},
                        {"id": 5, "name": "Garage", "status": 1},
                    ]
                },
            }
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _camera_route)
    payload = await getter.fetch()

    assert payload["synology.camera_1.name"] == "Driveway"
    assert payload["synology.camera_2.name"] == "Garage"
    assert "id=1" in str(payload["synology.camera_1.snapshot_url"])
    assert "id=5" in str(payload["synology.camera_2.snapshot_url"])
    assert "Old Cam" not in str(payload["synology.surveillance.status.rows"])


async def test_synology_getter_backup_installed_but_not_configured(monkeypatch) -> None:
    """Installed backup package with no tasks should report not configured."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        interval=1.0,
    )

    def _backup_not_configured(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        key = f"{api}.{method}"
        if key == "SYNO.Core.Package.list":
            return {
                "success": True,
                "data": {"packages": [{"id": "HyperBackup", "status": "running"}]},
            }
        if key == "SYNO.Backup.Task.list":
            return {"success": True, "data": {"tasks": []}}
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _backup_not_configured)
    payload = await getter.fetch()

    assert payload["synology.backup.installed"] == 1.0
    assert payload["synology.backup.configured"] == 0.0
    assert payload["synology.backup.summary"] == "not configured"


async def test_synology_getter_backup_reads_task_list_payload_shape(monkeypatch) -> None:
    """Backup detection should support task_list payload variants from Hyper Backup."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        interval=1.0,
    )

    def _backup_task_list_shape(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        version = str(query.get("version", ""))
        key = f"{api}.{method}.{version}"
        if key.startswith("SYNO.Core.Package.list"):
            return {
                "success": True,
                "data": {"packages": [{"id": "HyperBackup", "status": "running"}]},
            }
        if key == "SYNO.HyperBackup.Task.list.2":
            return {
                "success": True,
                "data": {
                    "task_list": [
                        {"name": "nas1 backup", "last_backup_result": "success"},
                        {"name": "offsite", "last_backup_result": "failed"},
                    ]
                },
            }
        if api in {"SYNO.Backup.Task", "SYNO.HyperBackup.Task", "SYNO.ActiveBackup.Task"}:
            return {"success": False, "error": {"code": 103}}
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _backup_task_list_shape)
    payload = await getter.fetch()

    assert payload["synology.backup.installed"] == 1.0
    assert payload["synology.backup.configured"] == 1.0
    assert payload["synology.backup.success"] == 0.0
    assert payload["synology.backup.summary"] == "1/2 ok, 1 failed"
    assert "nas1 backup|started|SUCCESS" in str(payload["synology.backup.rows"])
    assert "offsite|exited|FAILURE" in str(payload["synology.backup.rows"])


async def test_synology_getter_backup_prefers_last_result_over_status(monkeypatch) -> None:
    """Task enabled/running state should not override last-run success/failure."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        interval=1.0,
    )

    def _backup_last_result_priority(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        if f"{api}.{method}" == "SYNO.Core.Package.list":
            return {
                "success": True,
                "data": {"packages": [{"id": "HyperBackup", "status": "running"}]},
            }
        if f"{api}.{method}" == "SYNO.Backup.Task.list":
            return {
                "success": True,
                "data": {
                    "tasks": [
                        {"name": "nas1 backup", "status": "enabled", "last_bkp_result": "success"},
                        {"name": "offsite", "status": "enabled", "last_bkp_result": "failed"},
                    ]
                },
            }
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _backup_last_result_priority)
    payload = await getter.fetch()

    assert "nas1 backup|started|SUCCESS" in str(payload["synology.backup.rows"])
    assert "offsite|exited|FAILURE" in str(payload["synology.backup.rows"])


async def test_synology_getter_backup_uses_version_list_last_result(monkeypatch) -> None:
    """Version list status should drive last-run result when task status is non-result state."""

    getter = SynologyGetter(
        DataStore(),
        host="http://nas1:5000",
        username="demo",
        password="secret",
        interval=1.0,
    )

    def _backup_with_version_list(path: str, query: dict[str, object]) -> dict[str, object]:
        if path.endswith("/webapi/auth.cgi"):
            return {"success": True, "data": {"sid": "sid-abc"}}
        api = str(query.get("api", ""))
        method = str(query.get("method", ""))
        if f"{api}.{method}" == "SYNO.Core.Package.list":
            return {
                "success": True,
                "data": {"packages": [{"id": "HyperBackup", "status": "running"}]},
            }
        if f"{api}.{method}" == "SYNO.Backup.Task.list":
            return {
                "success": True,
                "data": {
                    "task_list": [
                        {"task_id": 1, "name": "nas1 backup", "status": "none"},
                    ]
                },
            }
        if f"{api}.{method}" == "SYNO.Backup.Version.list":
            return {
                "success": True,
                "data": {
                    "version_info_list": [
                        {"status": "success"},
                    ]
                },
            }
        return {"success": True, "data": {}}

    monkeypatch.setattr(getter, "_request_json", _backup_with_version_list)
    payload = await getter.fetch()

    assert payload["synology.backup.configured"] == 1.0
    assert payload["synology.backup.success"] == 1.0
    assert "nas1 backup|started|SUCCESS" in str(payload["synology.backup.rows"])
