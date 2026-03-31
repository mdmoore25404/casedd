"""Tests for :mod:`casedd.getters.ollama`."""

from __future__ import annotations

import asyncio
import json
from urllib.error import URLError

import pytest

from casedd.data_store import DataStore
from casedd.getter_health import GetterHealthRegistry
from casedd.getters.ollama import (
    OllamaDetailOptions,
    OllamaGetter,
    _running_models_rows,
)


class _RaisingURLOpen:
    def __call__(self, *args, **kwargs):
        raise URLError("connect refused")


class _FakeResponse:
    """Minimal context-managed HTTP response for urlopen monkeypatching."""

    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _OllamaDetailedUrlOpen:
    """Dispatch fixture payloads based on requested Ollama endpoint."""

    def __call__(self, req, timeout: float):
        url = str(req.full_url)
        if url.endswith("/api/ps"):
            return _FakeResponse(
                {
                    "models": [
                        {
                            "name": "llama3.2:latest",
                            "size": 7_500_000_000,
                            "size_vram": 5_100_000_000,
                            "expires_at": "2099-01-01T00:00:00Z",
                            "details": {
                                "family": "llama",
                                "parameter_size": "8B",
                                "quantization_level": "Q4_K_M",
                            },
                        }
                    ]
                }
            )
        if url.endswith("/api/tags"):
            return _FakeResponse(
                {
                    "models": [
                        {
                            "name": "llama3.2:latest",
                            "modified_at": "2026-03-30T10:11:12Z",
                            "size": 7_500_000_000,
                            "details": {
                                "family": "llama",
                                "parameter_size": "8B",
                                "quantization_level": "Q4_K_M",
                            },
                        },
                        {
                            "name": "qwen3:latest",
                            "modified_at": "2026-03-29T06:05:04Z",
                            "size": 4_200_000_000,
                            "details": {
                                "family": "qwen",
                                "parameter_size": "4B",
                                "quantization_level": "Q8_0",
                            },
                        },
                    ]
                }
            )
        if url.endswith("/api/version"):
            return _FakeResponse({"version": "0.6.0"})
        raise AssertionError(f"Unhandled URL: {url}")


class _OllamaEmptyUrlOpen:
    """Fixture with no local/running models."""

    def __call__(self, req, timeout: float):
        url = str(req.full_url)
        if url.endswith("/api/ps"):
            return _FakeResponse({"models": []})
        if url.endswith("/api/tags"):
            return _FakeResponse({"models": []})
        if url.endswith("/api/version"):
            return _FakeResponse({"version": "0.6.0"})
        raise AssertionError(f"Unhandled URL: {url}")


class _OllamaMalformedUrlOpen:
    """Fixture where /api/ps payload is malformed."""

    def __call__(self, req, timeout: float):
        url = str(req.full_url)
        if url.endswith("/api/ps"):
            return _FakeResponse({"models": "not-a-list"})
        raise AssertionError(f"Unhandled URL: {url}")


async def test_ollama_detailed_mode_emits_running_and_inventory(monkeypatch) -> None:
    """Detailed mode should emit version, counts, and enumerated model keys."""
    monkeypatch.setattr("casedd.getters.ollama.urlopen", _OllamaDetailedUrlOpen())

    getter = OllamaGetter(
        DataStore(),
        detail=OllamaDetailOptions(enabled=True, max_models=4),
    )
    payload = await getter.fetch()

    assert payload["ollama.version"] == "0.6.0"
    assert payload["ollama.models.local_count"] == 2.0
    assert payload["ollama.models.running_count"] == 1.0
    assert payload["ollama.running_1.name"] == "llama3.2"
    assert payload["ollama.running_1.size_bytes"] == 7_500_000_000.0
    assert payload["ollama.running_1.size_vram_bytes"] == 5_100_000_000.0
    assert payload["ollama.running_1.family"] == "llama"
    assert payload["ollama.model_2.name"] == "qwen3"
    assert payload["ollama.model_2.parameter_size"] == "4B"
    running_rows = str(payload["ollama.running.rows"])
    assert "Q4_K_M" in running_rows
    assert "8B" in running_rows


async def test_ollama_detailed_mode_empty_inventory(monkeypatch) -> None:
    """Detailed mode should emit zero-count inventory when no models exist."""
    monkeypatch.setattr("casedd.getters.ollama.urlopen", _OllamaEmptyUrlOpen())

    getter = OllamaGetter(DataStore(), detail=OllamaDetailOptions(enabled=True))
    payload = await getter.fetch()

    assert payload["ollama.models.local_count"] == 0.0
    assert payload["ollama.models.running_count"] == 0.0
    assert payload["ollama.models.rows"] == "—|—"
    assert payload["ollama.running.rows"] == "—|—"


async def test_ollama_malformed_payload_raises_runtime_error(monkeypatch) -> None:
    """Malformed /api/ps payload should raise RuntimeError for health tracking."""
    monkeypatch.setattr("casedd.getters.ollama.urlopen", _OllamaMalformedUrlOpen())

    getter = OllamaGetter(DataStore())
    with pytest.raises(RuntimeError, match="models"):
        await getter.fetch()


async def test_ollama_failure_marks_health_error(monkeypatch) -> None:
    """When Ollama's HTTP call fails the getter run loop records an error."""
    store = DataStore()
    registry = GetterHealthRegistry()

    getter = OllamaGetter(
        store,
        base_url="http://localhost:11435",
        interval=0.0,
        timeout=0.01,
    )
    getter.attach_health(registry)

    # Force urlopen to raise so _sample() raises and run() records the error.
    monkeypatch.setattr("casedd.getters.ollama.urlopen", _RaisingURLOpen())

    task = asyncio.create_task(getter.run())
    await asyncio.sleep(0.05)
    getter.stop()
    await asyncio.wait_for(task, timeout=1.0)

    snap = {e["name"]: e for e in registry.snapshot()}
    entry = snap.get("OllamaGetter")
    assert entry is not None
    assert entry["status"] == "error"


def test_running_models_rows_includes_quant_vram_and_ttl() -> None:
    """Running-model rows should include quantization, size, VRAM, and TTL."""
    rows = _running_models_rows(
        [
            {
                "name": "llama3.2:latest",
                "size_vram": 5_100_000_000,
                "expires_at": "2099-01-01T00:00:00Z",
                "details": {
                    "parameter_size": "8B",
                    "quantization_level": "Q4_K_M",
                },
            }
        ]
    )

    assert rows.startswith("llama3.2|")
    assert "Q4_K_M" in rows
    assert "8B" in rows
    assert "GB" in rows


def test_running_models_rows_uses_default_meta_when_fields_missing() -> None:
    """Running-model rows should include stable fallback VRAM/TTL metadata."""
    rows = _running_models_rows([
        {"name": "tiny:latest"},
    ])

    assert rows == "tiny|0.0GB n/a"
