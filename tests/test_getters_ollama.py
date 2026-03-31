"""Tests for Ollama getter health behaviour when the remote API is unavailable.

Ensures that connection failures surface as health errors (issue reported by
user: Ollama showed as 'ok' even when the local service was down).
"""

from __future__ import annotations

import asyncio
from urllib.error import URLError

from casedd.data_store import DataStore
from casedd.getter_health import GetterHealthRegistry
from casedd.getters.ollama import OllamaGetter


class _RaisingURLOpen:
    def __call__(self, *args, **kwargs):
        raise URLError("connect refused")


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
