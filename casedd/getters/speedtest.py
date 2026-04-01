"""Ookla speedtest CLI getter.

Runs the Ookla ``speedtest`` CLI on a configurable interval and publishes
results into the data store under the ``speedtest.*`` namespace.

When ``passive=True``, the local CLI is never invoked.  Speed test results
are expected to arrive via the REST ingestion endpoint
(``POST /api/update``) from an external machine.  The getter still
derives status and percentage fields from raw Mb/s readings pushed into
``speedtest.download_mbps`` and ``speedtest.upload_mbps`` by computing
them once on startup and writing them to the store as configuration hints
(thresholds, advertised speeds, reference speeds).  All derived metrics
must be computed by the pushing machine or accepted as-is from the payload.

Store keys written:
    - ``speedtest.download_mbps`` (float)
    - ``speedtest.upload_mbps`` (float)
    - ``speedtest.ping_ms`` (float)
    - ``speedtest.jitter_ms`` (float)
    - ``speedtest.download_pct_adv`` (float)
    - ``speedtest.upload_pct_adv`` (float)
    - ``speedtest.download_pct_ref`` (float)
    - ``speedtest.upload_pct_ref`` (float)
    - ``speedtest.download_status`` (str) -- good | marginal | critical
    - ``speedtest.upload_status`` (str) -- good | marginal | critical
    - ``speedtest.last_run`` (str) -- local timestamp
    - ``speedtest.summary`` (str)
    - ``speedtest.simple_summary`` (str)
    - ``speedtest.server_id`` (str)
    - ``speedtest.server_name`` (str)
    - ``speedtest.server_location`` (str)
    - ``speedtest.server_country`` (str)
    - ``speedtest.server_host`` (str)
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from typing import cast

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter
from casedd.speedtest_fields import enrich_speedtest_timestamp_fields, now_local_timestamp

_log = logging.getLogger(__name__)

_MEGABIT = 1_000_000.0


class SpeedtestGetter(BaseGetter):
    """Periodic speedtest getter backed by the Ookla CLI.

    When ``passive=True`` the local CLI is never invoked; speed test results
    must be pushed via ``POST /api/update`` from an external machine.

    Args:
        store: Shared data store instance.
        interval: Poll interval in seconds.
        passive: When ``True``, skip local CLI runs and accept pushed data only.
        binary: Speedtest binary name or absolute path.
        server_id: Optional Ookla server ID to force for test target.
        advertised_down_mbps: Advertised download speed used for % of plan.
        advertised_up_mbps: Advertised upload speed used for % of plan.
        reference_down_mbps: Optional effective downlink baseline in Mb/s.
        reference_up_mbps: Optional effective uplink baseline in Mb/s.
        marginal_ratio: Ratio below which status becomes ``marginal``.
        critical_ratio: Ratio below which status becomes ``critical``.
        startup_delay: Seconds to wait before first speedtest run.
    """

    def __init__(  # noqa: PLR0913 -- explicit config args keep callsites clear
        self,
        store: DataStore,
        interval: float = 1800.0,
        passive: bool = False,
        binary: str = "speedtest",
        server_id: str | None = None,
        advertised_down_mbps: float = 2000.0,
        advertised_up_mbps: float = 200.0,
        reference_down_mbps: float | None = None,
        reference_up_mbps: float | None = None,
        marginal_ratio: float = 0.9,
        critical_ratio: float = 0.7,
        startup_delay: float = 0.0,
    ) -> None:
        """Initialise the speedtest getter.

        Args:
            store: Shared data store.
            interval: Poll interval in seconds.
            passive: When ``True``, disable local CLI and accept pushed data only.
            binary: Speedtest binary path or executable name.
            server_id: Optional Ookla server ID to force.
            advertised_down_mbps: Advertised downlink speed.
            advertised_up_mbps: Advertised uplink speed.
            reference_down_mbps: Optional effective downlink baseline.
            reference_up_mbps: Optional effective uplink baseline.
            marginal_ratio: Marginal status threshold ratio.
            critical_ratio: Critical status threshold ratio.
            startup_delay: Delay before first run in seconds.
        """
        super().__init__(store, interval)
        self._binary = binary
        self._server_id = server_id
        self._advertised_down_mbps = advertised_down_mbps
        self._advertised_up_mbps = advertised_up_mbps
        self._reference_down_mbps = reference_down_mbps
        self._reference_up_mbps = reference_up_mbps
        self._marginal_ratio = marginal_ratio
        self._critical_ratio = critical_ratio
        self._startup_delay = startup_delay
        self._passive = passive
        self._enabled = (
            not passive
            and (shutil.which(binary) is not None or binary.startswith("/"))
        )

        if passive:
            _log.info(
                "Speedtest getter in passive mode — local CLI disabled."
                "  Push results via POST /api/update (speedtest.* keys).",
            )
        elif self._enabled:
            _log.info(
                "Speedtest getter active (binary=%s, interval=%.0fs, server_id=%s).",
                binary,
                interval,
                server_id or "auto",
            )
        else:
            _log.warning(
                "Speedtest binary '%s' not found; speedtest getter disabled.",
                binary,
            )

    async def run(self) -> None:
        """Run speedtest polling loop with optional startup delay.

        In passive mode the loop exits immediately after logging — the daemon
        keeps this getter registered so the ``speedtest.*`` namespace still
        triggers getter-awareness logic, but no CLI process is ever spawned.

        Returns:
            None
        """
        self._running = True
        name = type(self).__name__
        _log.info("Getter started: %s (interval=%.1fs)", name, self._interval)

        if self._passive:
            _log.info("%s: passive mode — waiting for pushed data only.", name)
            # Park the coroutine cheaply without spinning; data arrives via REST.
            while self._running:
                await asyncio.sleep(60.0)
            return

        if self._enabled and self._startup_delay > 0.0:
            _log.info("Speedtest first run delayed by %.0fs", self._startup_delay)
            await asyncio.sleep(self._startup_delay)

        while self._running:
            try:
                data = await self.fetch()
                if data:
                    self._store.update(data)
            except Exception:
                _log.warning("Getter %s raised an exception:", name, exc_info=True)

            await asyncio.sleep(self._interval)

    async def fetch(self) -> dict[str, StoreValue]:
        """Run one speedtest sample.

        Returns:
            Mapping of ``speedtest.*`` values, or empty mapping when disabled.
        """
        if not self._enabled:
            return {}
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Execute the speedtest CLI and parse JSON output.

        Returns:
            Mapping of ``speedtest.*`` store values.
        """
        try:
            command = [
                self._binary,
                "--accept-license",
                "--accept-gdpr",
                "--format=json",
            ]
            if self._server_id:
                command.append(f"--server-id={self._server_id}")

            completed = subprocess.run(  # noqa: S603 -- fixed command structure
                command,
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
            _log.warning("Speedtest execution failed: %s", exc)
            return {}

        try:
            payload = cast("dict[str, object]", json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            _log.warning("Failed to parse speedtest JSON output: %s", exc)
            return {}

        result = self._extract_metrics(payload)
        if result:
            down = float(result["speedtest.download_mbps"])
            up = float(result["speedtest.upload_mbps"])
            ping = float(result["speedtest.ping_ms"])
            jitter = float(result["speedtest.jitter_ms"])
            _log.info(
                "Speedtest sample: down=%.1f Mb/s up=%.1f Mb/s ping=%.1f ms jitter=%.1f ms",
                down,
                up,
                ping,
                jitter,
            )
        return result

    def _extract_metrics(self, payload: dict[str, object]) -> dict[str, StoreValue]:
        """Extract required fields from the speedtest JSON payload.

        Args:
            payload: Parsed speedtest JSON payload.

        Returns:
            Mapping of ``speedtest.*`` values. Empty mapping if required fields
            are missing.
        """
        download_obj = payload.get("download")
        upload_obj = payload.get("upload")
        ping_obj = payload.get("ping")

        if not isinstance(download_obj, dict):
            return {}
        if not isinstance(upload_obj, dict):
            return {}
        if not isinstance(ping_obj, dict):
            return {}

        download_bandwidth = _to_float(download_obj.get("bandwidth"))
        upload_bandwidth = _to_float(upload_obj.get("bandwidth"))
        ping_latency = _to_float(ping_obj.get("latency"))
        ping_jitter = _to_float(ping_obj.get("jitter"))

        if download_bandwidth is None or upload_bandwidth is None:
            return {}
        if ping_latency is None or ping_jitter is None:
            return {}

        download_mbps = (download_bandwidth * 8.0) / _MEGABIT
        upload_mbps = (upload_bandwidth * 8.0) / _MEGABIT

        ref_down_mbps = min(
            self._advertised_down_mbps,
            self._reference_down_mbps or self._advertised_down_mbps,
        )
        ref_up_mbps = min(
            self._advertised_up_mbps,
            self._reference_up_mbps or self._advertised_up_mbps,
        )

        download_pct = (download_mbps / self._advertised_down_mbps) * 100.0
        upload_pct = (upload_mbps / self._advertised_up_mbps) * 100.0
        download_pct_ref = (download_mbps / ref_down_mbps) * 100.0
        upload_pct_ref = (upload_mbps / ref_up_mbps) * 100.0

        # Status is judged against the reference baseline (effective link cap),
        # not the advertised plan, so a 944 Mb/s result on a 1 Gb/s-capped host
        # correctly shows as "good" even if the plan is 2.5 Gb/s.
        download_status = self._status_for_ratio(download_mbps / ref_down_mbps)
        upload_status = self._status_for_ratio(upload_mbps / ref_up_mbps)

        now = now_local_timestamp()
        summary = (
            f"Down {download_mbps:.1f} Mb/s ({download_status}) | "
            f"Up {upload_mbps:.1f} Mb/s ({upload_status}) | "
            f"Ping {ping_latency:.1f} ms | Jitter {ping_jitter:.1f} ms"
        )
        compact = (
            f"DL {download_mbps:.0f} | UL {upload_mbps:.0f} Mb/s\n"
            f"{ping_latency:.1f} ms / {ping_jitter:.1f} ms"
        )
        simple = f"{download_mbps:.0f} / {upload_mbps:.0f} Mb/s"

        server_id, server_name, server_location, server_country, server_host = (
            self._extract_server(payload)
        )

        values: dict[str, StoreValue] = {
            "speedtest.download_mbps": round(download_mbps, 2),
            "speedtest.upload_mbps": round(upload_mbps, 2),
            "speedtest.ping_ms": round(ping_latency, 2),
            "speedtest.jitter_ms": round(ping_jitter, 2),
            "speedtest.download_pct_adv": round(download_pct, 2),
            "speedtest.upload_pct_adv": round(upload_pct, 2),
            "speedtest.download_pct_ref": round(download_pct_ref, 2),
            "speedtest.upload_pct_ref": round(upload_pct_ref, 2),
            "speedtest.download_status": download_status,
            "speedtest.upload_status": upload_status,
            "speedtest.threshold_marginal_pct": round(self._marginal_ratio * 100.0, 1),
            "speedtest.threshold_critical_pct": round(self._critical_ratio * 100.0, 1),
            "speedtest.last_run": now,
            "speedtest.summary": summary,
            "speedtest.simple_summary": simple,
            "speedtest.compact_summary": compact,
            "speedtest.server_id": server_id,
            "speedtest.server_name": server_name,
            "speedtest.server_location": server_location,
            "speedtest.server_country": server_country,
            "speedtest.server_host": server_host,
        }
        enrich_speedtest_timestamp_fields(values)
        return values

    def _extract_server(
        self,
        payload: dict[str, object],
    ) -> tuple[str, str, str, str, str]:
        """Extract server metadata fields from speedtest JSON payload.

        Args:
            payload: Parsed speedtest JSON payload.

        Returns:
            Tuple of server_id, server_name, server_location, server_country,
            and server_host. Missing values are returned as empty strings.
        """
        server_obj = payload.get("server")
        if not isinstance(server_obj, dict):
            return ("", "", "", "", "")

        return (
            _to_string(server_obj.get("id")),
            _to_string(server_obj.get("name")),
            _to_string(server_obj.get("location")),
            _to_string(server_obj.get("country")),
            _to_string(server_obj.get("host")),
        )

    def _status_for_ratio(self, ratio: float) -> str:
        """Classify performance ratio against configured thresholds.

        Args:
            ratio: Measured speed / advertised speed ratio.

        Returns:
            ``critical``, ``marginal``, or ``good``.
        """
        if ratio < self._critical_ratio:
            return "critical"
        if ratio < self._marginal_ratio:
            return "marginal"
        return "good"


def _to_float(value: object) -> float | None:
    """Convert unknown JSON field values into floats.

    Args:
        value: Raw JSON field value.

    Returns:
        Parsed float or ``None`` when conversion fails.
    """
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_string(value: object) -> str:
    """Convert a JSON field to string, returning empty string for null/unknown."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return ""
