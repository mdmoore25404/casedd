"""Shared test fixtures for CASEDD HTTP API tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from casedd.data_store import DataStore
from casedd.outputs.http_viewer import HttpViewerOutput


def _make_client(
    *,
    api_key: str | None = None,
    api_rate_limit: int = 0,
    health_provider: Callable[[], dict[str, object]] | None = None,
    templates_dir: Path | None = None,
) -> tuple[TestClient, DataStore]:
    """Build a FastAPI TestClient wrapping :class:`HttpViewerOutput`.

    Args:
        api_key: Optional API key to enforce on update endpoints.
        api_rate_limit: Max requests/minute per IP (0 = disabled).
        health_provider: Optional callable returning a health snapshot dict.
        templates_dir: Directory to serve templates from (defaults to CWD/templates).

    Returns:
        Tuple of (TestClient, DataStore) for use in tests.
    """
    store = DataStore()
    tdir = templates_dir or Path("templates")
    panels: list[dict[str, object]] = [{"name": "main", "display_name": "Main"}]

    output = HttpViewerOutput(
        store=store,
        host="127.0.0.1",
        port=0,  # not actually bound — we use TestClient
        ws_port=8765,
        panels=panels,
        default_panel="main",
        viewer_bg="#111111",
        templates_dir=tdir,
        history_provider=dict,
        health_provider=health_provider,
        api_key=api_key,
        api_rate_limit=api_rate_limit,
    )
    # Access the private FastAPI app directly for TestClient injection
    client = TestClient(output._app, raise_server_exceptions=False)
    return client, store
