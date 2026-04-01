"""OS package update status getter.

Collects available package updates from the host package manager and publishes
normalized ``os_updates.*`` keys for templates and trigger/skip logic.

Supported managers:
- apt (Debian/Ubuntu/Mint)
- dnf (Fedora/RHEL)

Store keys written:
    - ``os_updates.manager`` (str)
    - ``os_updates.active`` (0/1)
    - ``os_updates.total_count`` (float)
    - ``os_updates.security_count`` (float)
    - ``os_updates.has_updates`` (0/1)
    - ``os_updates.has_security_updates`` (0/1)
    - ``os_updates.rows`` (newline-delimited ``name|version [SEC]`` rows)
    - ``os_updates.summary`` (short summary string)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import shutil
import subprocess

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)


# Common Linux architecture suffixes used in package identifiers.
_ARCH_SUFFIXES: frozenset[str] = frozenset(
    {
        "x86_64",
        "amd64",
        "aarch64",
        "arm64",
        "armhf",
        "armel",
        "i386",
        "i686",
        "noarch",
        "ppc64le",
        "s390x",
    }
)


@dataclass(frozen=True)
class _PackageUpdate:
    """One available package update row."""

    name: str
    version: str
    security: bool


@dataclass(frozen=True)
class _CommandResult:
    """Captured subprocess result used by parsers."""

    returncode: int
    stdout: str


def _strip_arch(name: str) -> str:
    """Return package name without architecture suffix when present."""
    left, sep, right = name.rpartition(".")
    if sep == "" or right not in _ARCH_SUFFIXES:
        return name
    return left


def _parse_apt_upgradable(text: str) -> list[_PackageUpdate]:
    """Parse ``apt list --upgradable`` output into package rows."""
    updates: list[_PackageUpdate] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("listing"):
            continue

        # Example:
        #   openssl/jammy-updates,jammy-security 3.0.2-0ubuntu1.20 amd64 [...]
        left, _, rest = line.partition(" ")
        if "/" not in left or not rest:
            continue

        name, _, channels = left.partition("/")
        version = rest.split(maxsplit=1)[0].strip()
        if not name or not version:
            continue

        security = "security" in channels.lower()
        updates.append(_PackageUpdate(name=name, version=version, security=security))
    return updates


def _parse_dnf_check_update(text: str) -> list[_PackageUpdate]:
    """Parse ``dnf check-update`` output into package rows."""
    updates: list[_PackageUpdate] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        lower = line.lower()
        if lower.startswith("last metadata expiration check"):
            continue
        if lower.endswith("packages"):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        name_with_arch = parts[0].strip()
        version = parts[1].strip()
        repo = parts[2].strip().lower()

        if not name_with_arch or not version:
            continue

        name = _strip_arch(name_with_arch)
        security = "security" in repo
        updates.append(_PackageUpdate(name=name, version=version, security=security))
    return updates


def _parse_dnf_security_nvras(text: str) -> set[str]:
    """Parse ``dnf updateinfo list security --updates`` and return package NVRAs."""
    matches: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("last metadata expiration check"):
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        candidate = parts[-1]
        if "." not in candidate or "-" not in candidate:
            continue
        matches.add(candidate)
    return matches


class OsUpdatesGetter(BaseGetter):
    """Getter for host OS package update status.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds.
        manager: Package manager mode (``auto``, ``apt``, or ``dnf``).
        max_rows: Maximum package rows written into ``os_updates.rows``.
    """

    def __init__(
        self,
        store: DataStore,
        interval: float = 900.0,
        manager: str = "auto",
        max_rows: int = 30,
    ) -> None:
        """Initialize getter configuration."""
        super().__init__(store, interval)
        self._manager_mode = manager.strip().lower() or "auto"
        self._max_rows = max_rows
        self._warned_unavailable = False

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one package update sample."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking package update sample implementation."""
        manager = self._resolve_manager()
        if manager == "unknown":
            if not self._warned_unavailable:
                _log.info(
                    "OS package updates getter inactive (no apt/dnf available); "
                    "waiting for external pushes under os_updates.*"
                )
                self._warned_unavailable = True
            return self._inactive_payload()

        updates = self._collect_apt_updates() if manager == "apt" else self._collect_dnf_updates()

        return self._build_payload(manager, updates)

    def _resolve_manager(self) -> str:
        """Return resolved manager name using config mode + binary availability."""
        apt_path = shutil.which("apt")
        dnf_path = shutil.which("dnf")

        if self._manager_mode == "apt":
            return "apt" if apt_path is not None else "unknown"
        if self._manager_mode == "dnf":
            return "dnf" if dnf_path is not None else "unknown"

        if apt_path is not None:
            return "apt"
        if dnf_path is not None:
            return "dnf"
        return "unknown"

    def _collect_apt_updates(self) -> list[_PackageUpdate]:
        """Return package update list from apt output."""
        result = self._run_command(["apt", "list", "--upgradable"])
        if result is None:
            return []
        return _parse_apt_upgradable(result.stdout)

    def _collect_dnf_updates(self) -> list[_PackageUpdate]:
        """Return package update list from dnf output with security enrichment."""
        check_result = self._run_command(["dnf", "-q", "check-update"])
        if check_result is None:
            return []
        if check_result.returncode not in {0, 100}:
            _log.debug("dnf check-update returned code %s", check_result.returncode)
            return []

        updates = _parse_dnf_check_update(check_result.stdout)
        security_result = self._run_command(
            ["dnf", "-q", "updateinfo", "list", "security", "--updates"]
        )
        if security_result is None or security_result.returncode not in {0, 100}:
            return updates

        security_nvras = _parse_dnf_security_nvras(security_result.stdout)
        if not security_nvras:
            return updates

        enriched: list[_PackageUpdate] = []
        for update in updates:
            is_security = update.security
            if not is_security:
                prefix = f"{update.name}-"
                is_security = any(item.startswith(prefix) for item in security_nvras)
            enriched.append(
                _PackageUpdate(
                    name=update.name,
                    version=update.version,
                    security=is_security,
                )
            )
        return enriched

    def _build_payload(self, manager: str, updates: list[_PackageUpdate]) -> dict[str, StoreValue]:
        """Normalize package rows and booleans into store payload."""
        total_count = len(updates)
        security_count = sum(1 for update in updates if update.security)
        shown_rows = updates[: self._max_rows]
        rendered_rows = [
            f"{update.name}|{update.version}{' [SEC]' if update.security else ''}"
            for update in shown_rows
        ]
        rows_text = "\n".join(rendered_rows) if rendered_rows else "No updates|—"

        return {
            "os_updates.manager": manager,
            "os_updates.active": 1,
            "os_updates.total_count": float(total_count),
            "os_updates.security_count": float(security_count),
            "os_updates.has_updates": 1 if total_count > 0 else 0,
            "os_updates.has_security_updates": 1 if security_count > 0 else 0,
            "os_updates.rows": rows_text,
            "os_updates.summary": (
                f"{total_count} updates ({security_count} security) via {manager}"
            ),
        }

    @staticmethod
    def _inactive_payload() -> dict[str, StoreValue]:
        """Return placeholder payload when no supported manager is available."""
        return {
            "os_updates.manager": "unknown",
            "os_updates.active": 0,
            "os_updates.total_count": 0.0,
            "os_updates.security_count": 0.0,
            "os_updates.has_updates": 0,
            "os_updates.has_security_updates": 0,
            "os_updates.rows": "No package manager|—",
            "os_updates.summary": "No supported package manager detected",
        }

    @staticmethod
    def _run_command(args: list[str]) -> _CommandResult | None:
        """Run command and capture stdout without using a shell."""
        if not args:
            return None
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed argv, shell disabled
                args,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            _log.debug("OS updates command failed: %s", args, exc_info=True)
            return None

        return _CommandResult(returncode=proc.returncode, stdout=proc.stdout)
