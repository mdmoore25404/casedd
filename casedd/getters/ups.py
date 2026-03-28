"""UPS telemetry getter.

Polls local UPS tooling and publishes normalized ``ups.*`` keys so templates can
render a single UPS status widget regardless of backend command format.

Supported command sources:
- ``apcaccess -u`` (APC UPS daemon)
- ``upsc <target>`` (Network UPS Tools)
- Optional custom command via config

Store keys written:
    - ``ups.status`` (str)
    - ``ups.battery_percent`` (float)
    - ``ups.load_percent`` (float)
    - ``ups.load_watts`` (float)
    - ``ups.runtime_minutes`` (float)
    - ``ups.input_voltage`` (float)
    - ``ups.input_frequency`` (float)
    - ``ups.last_change_ts`` (float)
    - ``ups.online`` (int: 0/1)
    - ``ups.on_battery`` (int: 0/1)
    - ``ups.low_battery`` (int: 0/1)
    - ``ups.charging`` (int: 0/1)
    - ``ups.present`` (int: 0/1)
"""

from __future__ import annotations

import asyncio
import logging
import re
import shlex
import shutil
import subprocess
import time

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_first_float(raw: str) -> float | None:
    """Extract first numeric token from a text field.

    Args:
        raw: Input text potentially containing a number and units.

    Returns:
        Parsed float when present, else ``None``.
    """
    match = _NUM_RE.search(raw)
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _status_flags(status_text: str) -> tuple[int, int, int, int]:
    """Convert a UPS status string into boolean-like flags.

    Args:
        status_text: Status text from backend command output.

    Returns:
        Tuple ``(online, on_battery, low_battery, charging)`` as ``0``/``1``.
    """
    normalized = status_text.strip().upper()
    tokens = set(normalized.split())
    online = int("ONLINE" in tokens or "OL" in tokens)
    on_battery = int("ONBATT" in tokens or "OB" in tokens)
    low_battery = int("LOWBATT" in tokens or "LB" in tokens)
    charging = int("CHARGING" in tokens or "CHRG" in tokens)
    return (online, on_battery, low_battery, charging)


class UpsGetter(BaseGetter):
    """Getter for UPS status and power telemetry.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds.
        command: Optional custom command string to execute.
        upsc_target: Target argument for ``upsc`` fallback.
    """

    def __init__(
        self,
        store: DataStore,
        interval: float = 5.0,
        command: str | None = None,
        upsc_target: str = "ups@localhost",
    ) -> None:
        """Initialize UPS getter configuration.

        Args:
            store: Shared CASEDD data store.
            interval: Poll interval seconds.
            command: Optional custom command string.
            upsc_target: Fallback NUT target for ``upsc``.
        """
        super().__init__(store, interval)
        self._custom_command = command.strip() if command is not None else ""
        self._upsc_target = upsc_target.strip() or "ups@localhost"
        self._apcaccess = shutil.which("apcaccess")
        self._upsc = shutil.which("upsc")
        self._warned_unavailable = False

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one UPS sample from the configured backend.

        Returns:
            Dict with normalized ``ups.*`` keys.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Blocking UPS sample implementation.

        Returns:
            Flattened UPS store updates.
        """
        parsed = self._read_backend()
        if parsed is None:
            if not self._warned_unavailable:
                _log.info(
                    "UPS getter: no supported backend detected (custom/apcaccess/upsc); "
                    "waiting for external pushes under ups.*"
                )
                self._warned_unavailable = True
            return {
                "ups.status": "unavailable",
                "ups.present": 0,
            }

        status = str(parsed.get("status", "UNKNOWN"))
        battery_percent = float(parsed.get("battery_percent", 0.0))
        load_percent = float(parsed.get("load_percent", 0.0))
        load_watts = float(parsed.get("load_watts", 0.0))
        runtime_minutes = float(parsed.get("runtime_minutes", 0.0))
        input_voltage = float(parsed.get("input_voltage", 0.0))
        input_frequency = float(parsed.get("input_frequency", 0.0))
        last_change_ts = float(parsed.get("last_change_ts", time.time()))

        online, on_battery, low_battery, charging = _status_flags(status)
        return {
            "ups.status": status,
            "ups.battery_percent": max(0.0, min(100.0, battery_percent)),
            "ups.load_percent": max(0.0, load_percent),
            "ups.load_watts": max(0.0, load_watts),
            "ups.runtime_minutes": max(0.0, runtime_minutes),
            "ups.input_voltage": max(0.0, input_voltage),
            "ups.input_frequency": max(0.0, input_frequency),
            "ups.last_change_ts": last_change_ts,
            "ups.online": online,
            "ups.on_battery": on_battery,
            "ups.low_battery": low_battery,
            "ups.charging": charging,
            "ups.present": 1,
        }

    def _read_backend(self) -> dict[str, float | str] | None:  # noqa: PLR0911
        """Execute the configured/available backend and parse output.

        Returns:
            Parsed normalized fields, or ``None`` when unavailable.
        """
        if self._custom_command:
            output = self._run_command(shlex.split(self._custom_command))
            if output is None:
                return None
            parsed_apc = self._parse_apcaccess(output)
            if parsed_apc is not None:
                return parsed_apc
            parsed_nut = self._parse_upsc(output)
            if parsed_nut is not None:
                return parsed_nut
            return None

        if self._apcaccess is not None:
            output = self._run_command([self._apcaccess, "-u"])
            if output is not None:
                parsed = self._parse_apcaccess(output)
                if parsed is not None:
                    return parsed

        if self._upsc is not None:
            output = self._run_command([self._upsc, self._upsc_target])
            if output is not None:
                parsed = self._parse_upsc(output)
                if parsed is not None:
                    return parsed

        return None

    @staticmethod
    def _run_command(args: list[str]) -> str | None:
        """Run a backend command safely.

        Args:
            args: Executable and arguments.

        Returns:
            Stdout text on success, else ``None``.
        """
        if not args:
            return None
        try:
            proc = subprocess.run(  # noqa: S603 -- fixed argv, shell disabled
                args,
                capture_output=True,
                text=True,
                timeout=6,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            _log.debug("UPS backend command failed: %s", args, exc_info=True)
            return None
        stdout = proc.stdout.strip()
        return stdout if stdout else None

    @staticmethod
    def _parse_key_value_lines(output: str) -> dict[str, str]:
        """Parse KEY: VALUE lines into a normalized mapping.

        Args:
            output: Raw backend command output.

        Returns:
            Lower-cased key map.
        """
        parsed: dict[str, str] = {}
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            parsed[key.strip().lower()] = value.strip()
        return parsed

    def _parse_apcaccess(self, output: str) -> dict[str, float | str] | None:
        """Parse ``apcaccess -u`` output.

        Args:
            output: Raw command output.

        Returns:
            Normalized UPS dict when parse succeeds, else ``None``.
        """
        data = self._parse_key_value_lines(output)
        if not data:
            return None

        status = data.get("status", "UNKNOWN")
        battery = _parse_first_float(data.get("bcharge", ""))
        load_pct = _parse_first_float(data.get("loadpct", ""))
        runtime = _parse_first_float(data.get("timeleft", ""))
        input_voltage = _parse_first_float(data.get("linev", ""))
        input_freq = _parse_first_float(data.get("linefreq", ""))
        nominal_power = _parse_first_float(data.get("nompower", ""))

        watts = 0.0
        if load_pct is not None and nominal_power is not None:
            watts = max(0.0, (load_pct / 100.0) * nominal_power)

        last_change = time.time()
        tonbatt = _parse_first_float(data.get("tonbatt", ""))
        if tonbatt is not None and tonbatt > 0:
            last_change = time.time() - tonbatt

        return {
            "status": status,
            "battery_percent": battery if battery is not None else 0.0,
            "load_percent": load_pct if load_pct is not None else 0.0,
            "load_watts": watts,
            "runtime_minutes": runtime if runtime is not None else 0.0,
            "input_voltage": input_voltage if input_voltage is not None else 0.0,
            "input_frequency": input_freq if input_freq is not None else 0.0,
            "last_change_ts": last_change,
        }

    def _parse_upsc(self, output: str) -> dict[str, float | str] | None:
        """Parse ``upsc`` output.

        Args:
            output: Raw command output.

        Returns:
            Normalized UPS dict when parse succeeds, else ``None``.
        """
        data = self._parse_key_value_lines(output)
        if not data:
            return None

        status = data.get("ups.status", data.get("status", "UNKNOWN"))
        battery = _parse_first_float(data.get("battery.charge", ""))
        load_pct = _parse_first_float(data.get("ups.load", ""))
        runtime_seconds = _parse_first_float(data.get("battery.runtime", ""))
        input_voltage = _parse_first_float(data.get("input.voltage", ""))
        input_freq = _parse_first_float(data.get("input.frequency", ""))
        watts = _parse_first_float(data.get("ups.realpower", ""))

        runtime_minutes = 0.0
        if runtime_seconds is not None:
            runtime_minutes = max(0.0, runtime_seconds / 60.0)

        return {
            "status": status,
            "battery_percent": battery if battery is not None else 0.0,
            "load_percent": load_pct if load_pct is not None else 0.0,
            "load_watts": watts if watts is not None else 0.0,
            "runtime_minutes": runtime_minutes,
            "input_voltage": input_voltage if input_voltage is not None else 0.0,
            "input_frequency": input_freq if input_freq is not None else 0.0,
            "last_change_ts": time.time(),
        }
