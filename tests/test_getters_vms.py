"""Tests for KVM/libvirt VM getter parsing and fallback behavior."""

from __future__ import annotations

from unittest.mock import patch

from casedd.data_store import DataStore
from casedd.getters.vms import VmGetter, format_uptime


def test_vm_getter_builds_vm_rows_and_summary() -> None:
    """Getter should emit summary + per-VM fields from virsh output."""
    list_stdout = (
        " Id   Name          State\n"
        "-----------------------------\n"
        " 1    media-vm      running\n"
        " -    build-vm      shut off\n"
    )
    dominfo_running = (
        "Id:             1\n"
        "Name:           media-vm\n"
        "OS Type:        hvm\n"
        "State:          running\n"
        "CPU(s):         2\n"
        "CPU time:       12.5s\n"
        "Max memory:     4194304 KiB\n"
        "Used memory:    1048576 KiB\n"
    )
    dominfo_stopped = (
        "Id:             -\n"
        "Name:           build-vm\n"
        "OS Type:        hvm\n"
        "State:          shut off\n"
        "CPU(s):         4\n"
        "CPU time:       0.0s\n"
        "Max memory:     8388608 KiB\n"
        "Used memory:    0 KiB\n"
    )

    def _run_side_effect(args: list[str], **_: object) -> object:
        class _CompletedProcess:
            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        if args[1:3] == ["list", "--all"]:
            return _CompletedProcess(list_stdout)
        if args[1:3] == ["dominfo", "media-vm"]:
            return _CompletedProcess(dominfo_running)
        return _CompletedProcess(dominfo_stopped)

    with (
        patch("casedd.getters.vms.shutil.which", return_value="/usr/bin/virsh"),
        patch("casedd.getters.vms.subprocess.run", side_effect=_run_side_effect),
    ):
        getter = VmGetter(DataStore(), max_items=3)
        payload = getter._sample()

    assert payload["vms.available"] == 1.0
    assert payload["vms.count_total"] == 2.0
    assert payload["vms.count_running"] == 1.0
    assert payload["vms.count_shutoff"] == 1.0
    assert payload["vms.1.name"] == "media-vm"
    assert payload["vms.1.state"] == "Running"
    assert payload["vms.2.name"] == "build-vm"
    assert "media-vm|Running" in str(payload["vms.rows"])


def test_vm_getter_unavailable_when_virsh_missing() -> None:
    """Getter should emit unavailable fallback when virsh is missing."""
    with patch("casedd.getters.vms.shutil.which", return_value=None):
        getter = VmGetter(DataStore())
        payload = getter._sample()

    assert payload["vms.mode"] == "unavailable"
    assert payload["vms.available"] == 0.0
    assert payload["vms.rows"]


def test_vm_getter_passive_mode() -> None:
    """Passive mode should avoid virsh polling and publish passive status."""
    with patch("casedd.getters.vms.shutil.which", return_value="/usr/bin/virsh"):
        getter = VmGetter(DataStore(), passive=True)
        payload = getter._sample()

    assert payload["vms.mode"] == "passive"
    assert payload["vms.available"] == 0.0


def test_format_uptime_compact_text() -> None:
    """Uptime formatter should emit day prefix for long durations."""
    assert format_uptime(0) == "00:00:00"
    assert format_uptime(3661) == "01:01:01"
    assert format_uptime(90061) == "1d 01:01:01"
