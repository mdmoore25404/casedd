"""Tests for :mod:`casedd.getters.hermes`."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from casedd.data_store import DataStore
from casedd.getters.hermes import HermesGetter, _format_model_display


def _write_state_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                model TEXT,
                billing_provider TEXT,
                billing_base_url TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                last_activity_at REAL
            );

            CREATE TABLE delivery_obligations (
                obligation_id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id TEXT,
                content TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                owner_pid INTEGER,
                owner_started_at INTEGER,
                last_error TEXT
            );

            CREATE TABLE async_delegations (
                delegation_id TEXT PRIMARY KEY,
                origin_session TEXT NOT NULL,
                origin_ui_session_id TEXT NOT NULL DEFAULT '',
                parent_session_id TEXT,
                state TEXT NOT NULL,
                dispatched_at REAL NOT NULL,
                completed_at REAL,
                updated_at REAL NOT NULL,
                event_json TEXT,
                result_json TEXT,
                delivery_state TEXT NOT NULL DEFAULT 'pending',
                delivery_attempts INTEGER NOT NULL DEFAULT 0,
                delivered_at REAL,
                owner_pid INTEGER,
                owner_started_at INTEGER,
                task_json TEXT,
                delivery_claim TEXT,
                delivery_claimed_at REAL,
                origin_session_id TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE compression_locks (
                session_id TEXT PRIMARY KEY,
                holder TEXT NOT NULL,
                acquired_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            INSERT INTO sessions (
                id,
                model,
                billing_provider,
                billing_base_url,
                started_at,
                ended_at,
                last_activity_at
            )
            VALUES
                (
                    's1',
                    'banditchat:latest',
                    'custom',
                    'http://bandit:11435/v1',
                    1700000000.0,
                    NULL,
                    1893437940.0
                ),
                (
                    's2',
                    'qwen3.5:latest',
                    'custom',
                    'http://bandit:11435/v1',
                    1700000100.0,
                    1700000200.0,
                    1893400000.0
                ),
                (
                    's3',
                    'grok-4.5',
                    'xai-oauth',
                    'https://api.x.ai/v1',
                    1700000300.0,
                    1700000400.0,
                    1700000400.0
                );

            INSERT INTO delivery_obligations (
                obligation_id,
                session_key,
                platform,
                chat_id,
                content,
                state,
                created_at,
                updated_at
            )
            VALUES
                ('o1', 'sk1', 'discord', 'c1', 'hello', 'pending', 1893456000.0, 1893456200.0),
                ('o2', 'sk2', 'slack', 'c2', 'done', 'delivered', 1893455000.0, 1893456100.0);

            INSERT INTO async_delegations (
                delegation_id,
                origin_session,
                state,
                dispatched_at,
                updated_at,
                delivery_state
            )
            VALUES
                ('d1', 's1', 'running', 1893456100.0, 1893456200.0, 'pending'),
                ('d2', 's2', 'completed', 1893450000.0, 1893451000.0, 'delivered');

            INSERT INTO compression_locks (
                session_id,
                holder,
                acquired_at,
                expires_at
            )
            VALUES
                ('s1', 'compressor', 1893456200.0, 1893456900.0);
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_hermes_getter_reads_local_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Getter should derive gateway, session, model, activity, and skill metrics."""
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "skills").mkdir()
    (hermes_home / "skills" / "alpha.py").write_text("", encoding="utf-8")
    (hermes_home / "skills" / "beta.md").write_text("", encoding="utf-8")
    (hermes_home / "skills" / "nested").mkdir()
    (hermes_home / "config.yaml").write_text(
                """
model:
  default: banditchat:latest
  provider: custom
  base_url: http://bandit:11435/v1
custom_providers:
  - name: Bandit (Ollama)
    base_url: http://bandit:11435/v1
    model: banditchat:latest
""".strip()
                + "\n",
        encoding="utf-8",
    )
    (hermes_home / "gateway_state.json").write_text(
        (
            '{"gateway_state":"running","pid":4321,'
            '"active_agents":2,'
            '"platforms":{'
            '"discord":{"state":"connected"},'
            '"slack":{"state":"error"}'
            '}}'
        ),
        encoding="utf-8",
    )
    _write_state_db(hermes_home / "state.db")

    monkeypatch.setattr("casedd.getters.hermes._pid_exists", lambda pid: pid == 4321)
    monkeypatch.setattr("casedd.getters.hermes._now_timestamp", lambda: 1893456300.0)

    getter = HermesGetter(DataStore(), hermes_home=hermes_home)
    payload = await getter.fetch()

    assert payload["hermes.gateway_up"] is True
    assert payload["hermes.sessions_active"] == 2.0
    assert payload["hermes.sessions_total"] == 3.0
    assert payload["hermes.model"] == "Ollama / banditchat"
    assert payload["hermes.latest_model"] == "Ollama / banditchat"
    assert payload["hermes.last_activity"] == "5H 6M"
    assert payload["hermes.skills_count"] == 2.0
    assert payload["hermes.platforms_connected"] == 1.0
    assert payload["hermes.platforms_total"] == 2.0
    assert payload["hermes.platforms_summary"] == "1/2 connected (discord:connected, slack:error)"
    assert payload["hermes.active_agents"] == 2.0
    assert payload["hermes.delivery_pending"] == 1.0
    assert payload["hermes.delivery_total"] == 2.0
    assert payload["hermes.delivery_summary"] == "1/2"
    assert payload["hermes.delegations_pending"] == 1.0
    assert payload["hermes.delegations_active"] == 1.0
    assert payload["hermes.delegations_total"] == 2.0
    assert payload["hermes.delegations_summary"] == "1 pending / 1 active / 2 total"
    assert payload["hermes.compression_locks"] == 1.0
    assert payload["hermes.compaction_busy"] is True
    assert payload["hermes.provider_aligned"] is True
    assert payload["hermes.provider_drift"] is False


@pytest.mark.asyncio
async def test_hermes_getter_missing_home_returns_safe_defaults(tmp_path: Path) -> None:
    """Missing Hermes files should produce a clean zero-value payload."""
    getter = HermesGetter(DataStore(), hermes_home=tmp_path / "missing")

    payload = await getter.fetch()

    assert payload == {
        "hermes.gateway_up": False,
        "hermes.sessions_active": 0.0,
        "hermes.sessions_total": 0.0,
        "hermes.model": "",
        "hermes.latest_model": "",
        "hermes.last_activity": "",
        "hermes.skills_count": 0.0,
        "hermes.platforms_connected": 0.0,
        "hermes.platforms_total": 0.0,
        "hermes.platforms_summary": "",
        "hermes.active_agents": 0.0,
        "hermes.delivery_pending": 0.0,
        "hermes.delivery_total": 0.0,
        "hermes.delivery_summary": "0/0",
        "hermes.delegations_pending": 0.0,
        "hermes.delegations_active": 0.0,
        "hermes.delegations_total": 0.0,
        "hermes.delegations_summary": "0 pending / 0 active / 0 total",
        "hermes.compression_locks": 0.0,
        "hermes.compaction_busy": False,
        "hermes.provider_aligned": False,
        "hermes.provider_drift": False,
    }


@pytest.mark.asyncio
async def test_hermes_getter_provider_drift_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured default provider/model should compare against the latest session."""
    hermes_home = tmp_path / "hermes-drift"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "\n".join(
            [
                "model:",
                "  default: grok-4.5",
                "  provider: xai-oauth",
                "  base_url: https://api.x.ai/v1",
                "custom_providers:",
                "  - name: Bandit (Ollama)",
                "    base_url: http://bandit:11435/v1",
                "    model: banditchat:latest",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_state_db(hermes_home / "state.db")
    monkeypatch.setattr("casedd.getters.hermes._now_timestamp", lambda: 1893456300.0)

    payload = await HermesGetter(DataStore(), hermes_home=hermes_home).fetch()

    assert payload["hermes.model"] == "xAI / grok-4.5"
    assert payload["hermes.latest_model"] == "Ollama / banditchat"
    assert payload["hermes.provider_aligned"] is False
    assert payload["hermes.provider_drift"] is True


def test_format_model_display_uses_fallback_provider() -> None:
    """Session-derived provider metadata should still format cleanly."""
    assert _format_model_display("xAI", "grok-4.5") == "xAI / grok-4.5"
