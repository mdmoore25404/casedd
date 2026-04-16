"""Tests for :mod:`casedd.getters.os_updates`."""

from __future__ import annotations

import subprocess

from casedd.data_store import DataStore
from casedd.getters.os_updates import (
    OsUpdatesGetter,
    _parse_apt_phased_packages,
    _parse_apt_upgradable,
    _parse_dnf_check_update,
    _parse_dnf_security_nvras,
)


def test_parse_apt_upgradable_marks_security_rows() -> None:
    """Apt parser should extract name/version and security hint from channels."""
    parsed = _parse_apt_upgradable(
        "\n".join(
            [
                "Listing...",
                "openssl/jammy-updates,jammy-security "
                "3.0.2-0ubuntu1.20 amd64 [upgradable from: 3.0.2-0ubuntu1.18]",
                "bash/jammy-updates 5.2.21-2ubuntu4 amd64 [upgradable from: 5.2.15-2]",
            ]
        )
    )

    assert len(parsed) == 2
    assert parsed[0].name == "openssl"
    assert parsed[0].version == "3.0.2-0ubuntu1.20"
    assert parsed[0].security is True
    assert parsed[1].name == "bash"
    assert parsed[1].security is False


def test_parse_apt_phased_packages_extracts_deferred_names() -> None:
    """Apt phased parser should return package names from deferred block."""
    phased = _parse_apt_phased_packages(
        "\n".join(
            [
                "Reading package lists... Done",
                "Calculating upgrade... Done",
                "The following upgrades have been deferred due to phasing:",
                "  firefox firefox-locale-en",
                "  linux-firmware",
                "",
                "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.",
            ]
        )
    )

    assert phased == {"firefox", "firefox-locale-en", "linux-firmware"}


def test_parse_dnf_outputs_extract_rows_and_security_nvras() -> None:
    """DNF parsers should read package rows and security advisory package refs."""
    check = _parse_dnf_check_update(
        "\n".join(
            [
                "Last metadata expiration check: 0:01:22 ago on Tue 01 Apr 2026 10:00:00.",
                "openssl.x86_64 3.2.2-1.fc41 updates",
                "bash.x86_64 5.2.26-1.fc41 updates-security",
            ]
        )
    )
    sec = _parse_dnf_security_nvras(
        "\n".join(
            [
                "FEDORA-2026-1234 Important/Sec. openssl-3.2.2-1.fc41.x86_64",
                "FEDORA-2026-9999 Moderate/Sec. curl-8.8.0-1.fc41.x86_64",
            ]
        )
    )

    assert len(check) == 2
    assert check[0].name == "openssl"
    assert check[0].security is False
    assert check[1].name == "bash"
    assert check[1].security is True
    assert "openssl-3.2.2-1.fc41.x86_64" in sec


async def test_os_updates_getter_apt_payload(monkeypatch) -> None:
    """Getter should emit booleans/counts/rows using apt output in auto mode."""

    def _which(name: str) -> str | None:
        if name == "apt":
            return "/usr/bin/apt"
        return None

    def _run(
        args: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert timeout == 20
        assert check is False
        if args == ["apt", "list", "--upgradable"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    "Listing...\n"
                    "openssl/jammy-updates,jammy-security 3.0.2-0ubuntu1.20 amd64\n"
                    "vim/jammy-updates 2:9.1.0016-1ubuntu7.8 amd64\n"
                    "firefox/jammy-updates 1:1snap1-0ubuntu2 amd64\n"
                ),
                stderr="",
            )
        if args == ["apt", "-s", "upgrade"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    "The following upgrades have been deferred due to phasing:\n"
                    "  firefox\n"
                ),
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr("casedd.getters.os_updates.shutil.which", _which)
    monkeypatch.setattr("casedd.getters.os_updates.subprocess.run", _run)

    getter = OsUpdatesGetter(DataStore(), manager="auto")
    payload = await getter.fetch()

    assert payload["os_updates.manager"] == "apt"
    assert payload["os_updates.active"] == 1
    assert payload["os_updates.total_count"] == 3.0
    assert payload["os_updates.security_count"] == 1.0
    assert payload["os_updates.has_updates"] == 1
    assert payload["os_updates.has_security_updates"] == 1
    assert payload["os_updates.phased_count"] == 1.0
    assert payload["os_updates.has_phased_updates"] == 1
    assert payload["os_updates.actionable_count"] == 2.0  # 3 total minus 1 phased
    assert payload["os_updates.has_actionable_updates"] == 1
    rows = str(payload["os_updates.rows"])
    assert "openssl|3.0.2-0ubuntu1.20 [SEC]" in rows
    assert "vim|2:9.1.0016-1ubuntu7.8" in rows
    assert "firefox|1:1snap1-0ubuntu2 (phasing)" in rows


async def test_os_updates_getter_dnf_security_enrichment(monkeypatch) -> None:
    """DNF security list should enrich security flags beyond repo-name hints."""

    def _which(name: str) -> str | None:
        if name == "dnf":
            return "/usr/bin/dnf"
        return None

    def _run(
        args: list[str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert timeout == 20
        assert check is False
        if args == ["dnf", "-q", "check-update"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=100,
                stdout=(
                    "openssl.x86_64 3.2.2-1.fc41 updates\n"
                    "bash.x86_64 5.2.26-1.fc41 updates\n"
                ),
                stderr="",
            )
        if args == ["dnf", "-q", "updateinfo", "list", "security", "--updates"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    "FEDORA-2026-1234 Important/Sec. "
                    "openssl-3.2.2-1.fc41.x86_64\n"
                ),
                stderr="",
            )
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr("casedd.getters.os_updates.shutil.which", _which)
    monkeypatch.setattr("casedd.getters.os_updates.subprocess.run", _run)

    getter = OsUpdatesGetter(DataStore(), manager="auto")
    payload = await getter.fetch()

    assert payload["os_updates.manager"] == "dnf"
    assert payload["os_updates.total_count"] == 2.0
    assert payload["os_updates.security_count"] == 1.0
    assert payload["os_updates.has_updates"] == 1
    assert payload["os_updates.has_security_updates"] == 1
    assert payload["os_updates.phased_count"] == 0.0
    assert payload["os_updates.has_phased_updates"] == 0
    assert payload["os_updates.actionable_count"] == 2.0
    assert payload["os_updates.has_actionable_updates"] == 1
    rows = str(payload["os_updates.rows"])
    assert "openssl|3.2.2-1.fc41 [SEC]" in rows
    assert "bash|5.2.26-1.fc41" in rows


async def test_os_updates_getter_inactive_without_supported_manager(monkeypatch) -> None:
    """Getter should emit inactive payload when apt/dnf binaries are absent."""

    def _which(_name: str) -> None:
        return None

    monkeypatch.setattr("casedd.getters.os_updates.shutil.which", _which)

    getter = OsUpdatesGetter(DataStore(), manager="auto")
    payload = await getter.fetch()

    assert payload["os_updates.manager"] == "unknown"
    assert payload["os_updates.active"] == 0
    assert payload["os_updates.has_updates"] == 0
    assert payload["os_updates.has_security_updates"] == 0
    assert payload["os_updates.has_phased_updates"] == 0
    assert payload["os_updates.actionable_count"] == 0.0
    assert payload["os_updates.has_actionable_updates"] == 0


async def test_os_updates_getter_all_phased_has_no_actionable(monkeypatch) -> None:
    """When every pending update is held for phasing, actionable_count must be 0.

    Rotation skip_if for os_updates uses ``actionable_count == 0`` so the
    template is suppressed when every pending package is phasing-held.  This
    matches the design intent: only show os_updates in the rotation when there
    is at least one package the user can actually act on.

    has_updates remains 1 (there ARE updates; they are just held back), but
    the template should still be skipped in the rotation until a non-phased
    update appears.  The display correctly shows phased packages (with amber
    suffix) when the template IS showing — e.g. if mixed phased+actionable.
    """

    def _which(name: str) -> str | None:
        return "/usr/bin/apt" if name == "apt" else None

    apt_list_output = "\n".join([
        "Listing...",
        "firefox/jammy-updates 125.0-0ubuntu1 amd64 "
        "[upgradable from: 124.0-0ubuntu1]",
        "linux-firmware/jammy-updates 20240318.git3b128b60-0ubuntu1 amd64 "
        "[upgradable from: 20231211.git4a51cc8a-0ubuntu2]",
    ])
    # Both packages are deferred due to phasing
    apt_sim_output = "\n".join([
        "Reading package lists... Done",
        "Calculating upgrade... Done",
        "The following upgrades have been deferred due to phasing:",
        "  firefox linux-firmware",
        "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.",
    ])

    def _run(
        args: list[str],
        capture_output: bool = False,
        text: bool = False,
        timeout: int = 20,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if "--upgradable" in args:
            return subprocess.CompletedProcess(args, 0, apt_list_output, "")
        return subprocess.CompletedProcess(args, 0, apt_sim_output, "")

    monkeypatch.setattr("casedd.getters.os_updates.shutil.which", _which)
    monkeypatch.setattr("casedd.getters.os_updates.subprocess.run", _run)

    getter = OsUpdatesGetter(DataStore(), manager="auto")
    payload = await getter.fetch()

    # total_count includes phased; actionable_count must be zero
    assert payload["os_updates.total_count"] == 2.0
    assert payload["os_updates.phased_count"] == 2.0
    assert payload["os_updates.actionable_count"] == 0.0
    assert payload["os_updates.has_actionable_updates"] == 0
    # has_updates is still 1 (there ARE updates, they're just held back)
    assert payload["os_updates.has_updates"] == 1
