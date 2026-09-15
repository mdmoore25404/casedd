"""Shelly smart plug getter.

Polls a local Shelly Gen2+ RPC endpoint with HTTP Digest authentication and
publishes read-only switch and power telemetry under the ``shelly.*`` namespace.

Store keys written:
    - ``shelly.output`` (1 when on, otherwise 0)
    - ``shelly.power`` (watts)
    - ``shelly.voltage`` (volts)
    - ``shelly.current`` (amps)
    - ``shelly.frequency`` (hertz)
    - ``shelly.energy`` (kilowatt-hours)
    - ``shelly.temperature_c`` (degrees Celsius)
    - ``shelly.temperature_f`` (degrees Fahrenheit)
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPDigestAuthHandler,
    HTTPPasswordMgrWithDefaultRealm,
    Request,
    build_opener,
)

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter


def _number(payload: Mapping[str, object], key: str) -> float | None:
    """Return a numeric payload field without accepting booleans."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


class ShellyGetter(BaseGetter):
    """Poll one local Shelly switch through its RPC API.

    Args:
        store: Shared data store.
        host: Shelly hostname, IP address, or base URL.
        auth_env: Environment variable containing ``username:password``.
        switch_id: Shelly switch component ID.
        interval: Poll interval in seconds.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(  # noqa: PLR0913 -- explicit device settings keep config wiring clear
        self,
        store: DataStore,
        host: str,
        auth_env: str = "SHELLY_AUTH",
        switch_id: int = 0,
        interval: float = 5.0,
        timeout: float = 4.0,
    ) -> None:
        """Initialise the Shelly getter settings."""
        super().__init__(store, interval)
        normalized_host = host.strip().rstrip("/")
        if normalized_host and "://" not in normalized_host:
            normalized_host = f"http://{normalized_host}"
        if normalized_host and urlsplit(normalized_host).scheme not in {"http", "https"}:
            msg = "Shelly host must use http or https"
            raise ValueError(msg)
        self._base_url = normalized_host
        self._auth_env = auth_env.strip()
        self._switch_id = switch_id
        self._timeout = timeout

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one Shelly switch status sample."""
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Perform the blocking authenticated RPC request."""
        if not self._base_url:
            msg = "Shelly host is not configured"
            raise RuntimeError(msg)
        if not self._auth_env:
            msg = "Shelly auth environment variable name is not configured"
            raise RuntimeError(msg)

        credentials = os.environ.get(self._auth_env, "")
        username, separator, password = credentials.partition(":")
        if not separator or not username or not password:
            msg = (
                f"Shelly credentials are missing or invalid in environment "
                f"variable {self._auth_env}"
            )
            raise RuntimeError(msg)

        url = f"{self._base_url}/rpc/Switch.GetStatus?id={self._switch_id}"
        password_manager = HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(None, self._base_url, username, password)
        opener = build_opener(HTTPDigestAuthHandler(password_manager))
        # The constructor restricts the base URL to HTTP(S).
        request = Request(  # noqa: S310
            url,
            headers={"Accept": "application/json"},
        )

        try:
            with opener.open(
                request,
                timeout=self._timeout,
            ) as response:
                payload_obj = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            msg = f"Shelly HTTP error {exc.code}"
            raise RuntimeError(msg) from exc
        except URLError as exc:
            msg = f"Shelly connection failed: {exc.reason}"
            raise RuntimeError(msg) from exc
        except json.JSONDecodeError as exc:
            msg = "Shelly returned invalid JSON"
            raise RuntimeError(msg) from exc

        if not isinstance(payload_obj, Mapping):
            msg = "Shelly returned an invalid status payload"
            raise RuntimeError(msg)
        return self._parse_status(payload_obj)

    @staticmethod
    def _parse_status(payload: Mapping[str, object]) -> dict[str, StoreValue]:
        """Normalize a ``Switch.GetStatus`` response into store keys."""
        result: dict[str, StoreValue] = {}
        output = payload.get("output")
        if isinstance(output, bool):
            result["shelly.output"] = int(output)

        for source_key, store_key in (
            ("apower", "shelly.power"),
            ("voltage", "shelly.voltage"),
            ("current", "shelly.current"),
            ("freq", "shelly.frequency"),
        ):
            value = _number(payload, source_key)
            if value is not None:
                result[store_key] = value

        energy = payload.get("aenergy")
        if isinstance(energy, Mapping):
            total_wh = _number(energy, "total")
            if total_wh is not None:
                result["shelly.energy"] = total_wh / 1000.0

        temperature = payload.get("temperature")
        if isinstance(temperature, Mapping):
            temperature_c = _number(temperature, "tC")
            temperature_f = _number(temperature, "tF")
            if temperature_c is not None:
                result["shelly.temperature_c"] = temperature_c
            if temperature_f is not None:
                result["shelly.temperature_f"] = temperature_f

        return result
