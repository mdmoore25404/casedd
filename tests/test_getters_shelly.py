"""Tests for the local Shelly smart plug getter."""

from __future__ import annotations

import asyncio
from io import BytesIO
from urllib.request import Request

import pytest

from casedd.data_store import DataStore
from casedd.getters.shelly import ShellyGetter


class _Response:
    """Context-managed byte response used by the opener test double."""

    def __init__(self, payload: bytes) -> None:
        self._body = BytesIO(payload)

    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        return None

    def read(self) -> bytes:
        """Return the configured response body."""
        return self._body.read()


class _Opener:
    """Capture the generated Shelly request and return a fixed response."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.request: Request | None = None
        self.timeout: float | None = None

    def open(self, request: Request, timeout: float) -> _Response:
        """Record request arguments and return the test response."""
        self.request = request
        self.timeout = timeout
        return _Response(self._payload)


def test_parse_status_maps_gen4_power_metrics() -> None:
    """Gen4 switch status fields should map to display-ready values."""
    payload: dict[str, object] = {
        "output": True,
        "apower": 146.3,
        "voltage": 123.0,
        "current": 1.191,
        "freq": 60.0,
        "aenergy": {"total": 59.309},
        "temperature": {"tC": 42.1, "tF": 107.8},
    }

    result = ShellyGetter._parse_status(payload)

    assert result == {
        "shelly.output": 1,
        "shelly.power": 146.3,
        "shelly.voltage": 123.0,
        "shelly.current": 1.191,
        "shelly.frequency": 60.0,
        "shelly.energy": pytest.approx(0.059309),
        "shelly.temperature_c": 42.1,
        "shelly.temperature_f": 107.8,
    }


def test_sample_uses_named_auth_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials should come from the configured env var, not YAML."""
    opener = _Opener(
        b'{"output":false,"apower":0,"voltage":123.1,"current":0.0}'
    )
    monkeypatch.setenv("TEST_SHELLY_AUTH", "admin:secret")
    monkeypatch.setattr("casedd.getters.shelly.build_opener", lambda handler: opener)
    getter = ShellyGetter(
        DataStore(),
        host="bandit-shelly",
        auth_env="TEST_SHELLY_AUTH",
        switch_id=2,
        timeout=3.0,
    )

    result = getter._sample()

    assert result["shelly.output"] == 0
    assert result["shelly.power"] == 0.0
    assert opener.request is not None
    assert opener.request.full_url == (
        "http://bandit-shelly/rpc/Switch.GetStatus?id=2"
    )
    assert opener.timeout == 3.0


def test_fetch_rejects_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing credential variable should fail explicitly."""
    monkeypatch.delenv("MISSING_SHELLY_AUTH", raising=False)
    getter = ShellyGetter(
        DataStore(),
        host="bandit-shelly",
        auth_env="MISSING_SHELLY_AUTH",
    )

    with pytest.raises(RuntimeError, match="MISSING_SHELLY_AUTH"):
        asyncio.run(getter.fetch())
