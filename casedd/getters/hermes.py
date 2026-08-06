"""Local Hermes status getter.

Polls only local Hermes state under ``~/.hermes`` and publishes lightweight
status data under the ``hermes.*`` namespace.

Public API:
    - :class:`HermesGetter` — local Hermes gateway/session/skills telemetry
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import yaml

from casedd.data_store import DataStore, StoreValue
from casedd.getters.base import BaseGetter

_log = logging.getLogger(__name__)

_SESSION_RECENT_WINDOW_SECONDS = 86400.0
_DELIVERY_DONE_STATES = {"delivered", "completed", "sent"}
_DELEGATION_DONE_STATES = {"completed", "failed", "cancelled", "expired"}
_DELEGATION_PENDING_DELIVERY_STATES = {"pending", "claimed"}


class HermesGetter(BaseGetter):
    """Read local Hermes status from filesystem and SQLite state.

    Args:
        store: Shared data store.
        hermes_home: Hermes home directory to inspect.
        interval: Poll interval in seconds.
    """

    def __init__(
        self,
        store: DataStore,
        hermes_home: Path = Path("~/.hermes"),
        interval: float = 15.0,
    ) -> None:
        """Initialise the Hermes getter.

        Args:
            store: Shared data store instance.
            hermes_home: Hermes home directory.
            interval: Poll interval in seconds.
        """
        super().__init__(store, interval)
        self._hermes_home = hermes_home.expanduser()

    async def fetch(self) -> dict[str, StoreValue]:
        """Collect one local Hermes status snapshot.

        Returns:
            Mapping of ``hermes.*`` keys with safe defaults when files are absent.
        """
        return await asyncio.to_thread(self._sample)

    def _sample(self) -> dict[str, StoreValue]:
        """Read local Hermes state from filesystem and SQLite."""
        gateway_state = self._gateway_state()
        skills_count = self._skills_count()
        config_state = self._config_state()
        session_state = self._session_state()
        config_model = config_state.default_model_display
        latest_provider_name = _resolve_provider_name(
            provider_id=session_state.latest_provider,
            base_url=session_state.latest_base_url,
            model_name=session_state.latest_model,
            custom_providers=config_state.custom_providers,
        )
        latest_model_display = _format_model_display(
            latest_provider_name,
            session_state.latest_model,
            session_state.latest_base_url,
        )
        model = config_model or latest_model_display
        provider_drift = bool(
            config_model
            and latest_model_display
            and latest_model_display != config_model
        )
        provider_aligned = bool(
            config_model
            and latest_model_display
            and latest_model_display == config_model
        )

        return {
            "hermes.gateway_up": gateway_state.gateway_up,
            "hermes.sessions_active": float(session_state.active_sessions),
            "hermes.sessions_total": float(session_state.total_sessions),
            "hermes.model": model,
            "hermes.latest_model": latest_model_display,
            "hermes.last_activity": session_state.last_activity,
            "hermes.skills_count": float(skills_count),
            "hermes.platforms_connected": float(gateway_state.platforms_connected),
            "hermes.platforms_total": float(gateway_state.platforms_total),
            "hermes.platforms_summary": gateway_state.platforms_summary,
            "hermes.active_agents": float(gateway_state.active_agents),
            "hermes.delivery_pending": float(session_state.delivery_pending),
            "hermes.delivery_total": float(session_state.delivery_total),
            "hermes.delivery_summary": _format_fraction(
                session_state.delivery_pending,
                session_state.delivery_total,
            ),
            "hermes.delegations_pending": float(session_state.delegations_pending),
            "hermes.delegations_active": float(session_state.delegations_active),
            "hermes.delegations_total": float(session_state.delegations_total),
            "hermes.delegations_summary": _format_delegations_summary(
                session_state.delegations_pending,
                session_state.delegations_active,
                session_state.delegations_total,
            ),
            "hermes.compression_locks": float(session_state.compression_locks),
            "hermes.compaction_busy": session_state.compression_locks > 0,
            "hermes.provider_aligned": provider_aligned,
            "hermes.provider_drift": provider_drift,
        }

    def _gateway_state(self) -> _HermesGatewayState:
        """Return Hermes gateway liveness and platform summary."""
        state = self._read_gateway_state()
        gateway_up = self._gateway_up_from_state(state)
        active_agents = 0
        platforms_connected = 0
        platforms_total = 0
        platforms_summary = ""

        if state is not None:
            active_agents = _gateway_active_agents(state)
            (
                platforms_connected,
                platforms_total,
                platforms_summary,
            ) = _gateway_platform_summary(state)

        return _HermesGatewayState(
            gateway_up=gateway_up,
            active_agents=active_agents,
            platforms_connected=platforms_connected,
            platforms_total=platforms_total,
            platforms_summary=platforms_summary,
        )

    def _gateway_up(self) -> bool:
        """Return whether the Hermes gateway appears alive."""
        return self._gateway_up_from_state(self._read_gateway_state())

    def _gateway_up_from_state(self, state: dict[str, object] | None) -> bool:
        """Return whether the Hermes gateway appears alive from cached state."""
        gateway_up = False
        if state is not None:
            state_value = state.get("gateway_state")
            if isinstance(state_value, str) and state_value.strip().lower() == "running":
                gateway_up = True
            else:
                pid_value = state.get("pid")
                if isinstance(pid_value, int) and _pid_exists(pid_value):
                    gateway_up = True

        if gateway_up:
            return True

        pid_path = self._hermes_home / "gateway.pid"
        if not pid_path.is_file():
            return False

        try:
            pid_text = pid_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            _log.debug("Failed reading Hermes gateway pid file %s: %s", pid_path, exc)
            return False

        if not pid_text:
            return False

        try:
            pid = int(pid_text)
        except ValueError:
            return False
        return _pid_exists(pid)

    def _read_gateway_state(self) -> dict[str, object] | None:
        """Return parsed gateway state JSON when present."""
        path = self._hermes_home / "gateway_state.json"
        if not path.is_file():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.debug("Failed reading Hermes gateway state %s: %s", path, exc)
            return None

        if isinstance(payload, dict):
            return payload
        return None

    def _skills_count(self) -> int:
        """Count top-level Hermes skill files."""
        skills_dir = self._hermes_home / "skills"
        if not skills_dir.is_dir():
            return 0

        try:
            return sum(1 for path in skills_dir.iterdir() if path.is_file())
        except OSError as exc:
            _log.debug("Failed counting Hermes skills in %s: %s", skills_dir, exc)
            return 0

    def _config_state(self) -> _HermesConfigState:
        """Read default model state from Hermes config."""
        config_path = self._hermes_home / "config.yaml"
        if not config_path.is_file():
            return _HermesConfigState.empty()

        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            _log.debug("Failed reading Hermes config %s: %s", config_path, exc)
            return _HermesConfigState.empty()

        if not isinstance(raw, dict):
            return _HermesConfigState.empty()

        model_obj = raw.get("model")
        if not isinstance(model_obj, dict):
            return _HermesConfigState.empty()

        model_name = _mapping_str(model_obj, "default")
        provider_name = _resolve_provider_name(
            provider_id=_mapping_str(model_obj, "provider"),
            base_url=_mapping_str(model_obj, "base_url"),
            model_name=model_name,
            custom_providers=raw.get("custom_providers"),
        )
        return _HermesConfigState(
            default_model_display=_format_model_display(provider_name, model_name),
            custom_providers=raw.get("custom_providers", ()),
        )

    def _session_state(self) -> _HermesSessionState:
        """Read session counters and latest activity from Hermes state.db."""
        db_path = self._hermes_home / "state.db"
        if not db_path.is_file():
            return _HermesSessionState.empty()

        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                counts_row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_sessions,
                        SUM(
                            CASE
                                WHEN ended_at IS NULL
                                OR COALESCE(last_activity_at, started_at, 0) >= ?
                                THEN 1
                                ELSE 0
                            END
                        ) AS active_sessions
                    FROM sessions
                    """,
                    (_now_timestamp() - _SESSION_RECENT_WINDOW_SECONDS,),
                ).fetchone()
                latest_row = conn.execute(
                    """
                    SELECT
                        model,
                        billing_provider,
                        billing_base_url,
                        COALESCE(last_activity_at, started_at) AS activity_at
                    FROM sessions
                    ORDER BY COALESCE(last_activity_at, started_at) DESC
                    LIMIT 1
                    """
                ).fetchone()
                delivery_rows = conn.execute(
                    """
                    SELECT state, COUNT(*) AS count
                    FROM delivery_obligations
                    GROUP BY state
                    """
                ).fetchall()
                delegation_rows = conn.execute(
                    """
                    SELECT state, delivery_state, COUNT(*) AS count
                    FROM async_delegations
                    GROUP BY state, delivery_state
                    """
                ).fetchall()
                compression_row = conn.execute(
                    """
                    SELECT COUNT(*) AS lock_count
                    FROM compression_locks
                    WHERE expires_at >= ?
                    """,
                    (_now_timestamp(),),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            _log.debug("Failed reading Hermes session db %s: %s", db_path, exc)
            return _HermesSessionState.empty()

        total_sessions = _row_int(counts_row, "total_sessions")
        active_sessions = _row_int(counts_row, "active_sessions")
        latest_model = _row_str(latest_row, "model")
        latest_provider = _row_str(latest_row, "billing_provider")
        latest_base_url = _row_str(latest_row, "billing_base_url")
        last_activity = _format_relative_age(_row_float(latest_row, "activity_at"))
        delivery_pending, delivery_total = _delivery_counts(delivery_rows)
        delegations_pending, delegations_active, delegations_total = _delegation_counts(
            delegation_rows,
        )
        compression_locks = _row_int(compression_row, "lock_count")
        return _HermesSessionState(
            total_sessions=total_sessions,
            active_sessions=active_sessions,
            latest_model=latest_model,
            latest_provider=latest_provider,
            latest_base_url=latest_base_url,
            last_activity=last_activity,
            delivery_pending=delivery_pending,
            delivery_total=delivery_total,
            delegations_pending=delegations_pending,
            delegations_active=delegations_active,
            delegations_total=delegations_total,
            compression_locks=compression_locks,
        )


@dataclass(frozen=True)
class _HermesGatewayState:
    """Computed Hermes gateway summary."""

    gateway_up: bool
    active_agents: int
    platforms_connected: int
    platforms_total: int
    platforms_summary: str


@dataclass(frozen=True)
class _HermesConfigState:
    """Computed Hermes config summary."""

    default_model_display: str
    custom_providers: object

    @classmethod
    def empty(cls) -> _HermesConfigState:
        """Return the zero-value Hermes config summary."""
        return cls(default_model_display="", custom_providers=())


@dataclass(frozen=True)
class _HermesSessionState:
    """Computed Hermes session summary."""

    total_sessions: int
    active_sessions: int
    latest_model: str
    latest_provider: str
    latest_base_url: str
    last_activity: str
    delivery_pending: int
    delivery_total: int
    delegations_pending: int
    delegations_active: int
    delegations_total: int
    compression_locks: int

    @classmethod
    def empty(cls) -> _HermesSessionState:
        """Return the zero-value Hermes session summary."""
        return cls(
            total_sessions=0,
            active_sessions=0,
            latest_model="",
            latest_provider="",
            latest_base_url="",
            last_activity="",
            delivery_pending=0,
            delivery_total=0,
            delegations_pending=0,
            delegations_active=0,
            delegations_total=0,
            compression_locks=0,
        )


def _now_timestamp() -> float:
    """Return current UTC timestamp as seconds since epoch."""
    return datetime.now(tz=UTC).timestamp()


def _row_int(row: sqlite3.Row | None, key: str) -> int:
    """Return an integer field from a SQLite row with safe fallback."""
    if row is None:
        return 0
    value = row[key]
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _row_float(row: sqlite3.Row | None, key: str) -> float | None:
    """Return a float field from a SQLite row with safe fallback."""
    if row is None:
        return None
    value = row[key]
    if isinstance(value, int | float):
        return float(value)
    return None


def _row_str(row: sqlite3.Row | None, key: str) -> str:
    """Return a string field from a SQLite row with safe fallback."""
    if row is None:
        return ""
    value = row[key]
    if isinstance(value, str):
        return value.strip()
    return ""


def _delivery_counts(rows: list[sqlite3.Row]) -> tuple[int, int]:
    """Return pending/total delivery obligation counts."""
    pending = 0
    total = 0
    for row in rows:
        count = _row_value_int(row, "count")
        total += count
        state = _row_value_str(row, "state").lower()
        if state not in _DELIVERY_DONE_STATES:
            pending += count
    return pending, total


def _delegation_counts(rows: list[sqlite3.Row]) -> tuple[int, int, int]:
    """Return pending, active, and total async delegation counts."""
    pending = 0
    active = 0
    total = 0
    for row in rows:
        count = _row_value_int(row, "count")
        total += count
        state = _row_value_str(row, "state").lower()
        delivery_state = _row_value_str(row, "delivery_state").lower()
        if state and state not in _DELEGATION_DONE_STATES:
            active += count
        if delivery_state in _DELEGATION_PENDING_DELIVERY_STATES:
            pending += count
    return pending, active, total


def _row_value_int(row: sqlite3.Row, key: str) -> int:
    """Return a required integer-ish row value."""
    value = row[key]
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _row_value_str(row: sqlite3.Row, key: str) -> str:
    """Return a required string row value."""
    value = row[key]
    if isinstance(value, str):
        return value.strip()
    return ""


def _format_relative_age(timestamp: float | None) -> str:
    """Format a Unix timestamp as a compact elapsed time string."""
    if timestamp is None or timestamp <= 0.0:
        return ""
    elapsed_seconds = max(0, int(_now_timestamp() - timestamp))
    days, remainder = divmod(elapsed_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    if days > 0:
        return f"{days}D {hours}H {minutes}M"
    if hours > 0:
        return f"{hours}H {minutes}M"
    return f"{minutes}M"


def _format_fraction(value: int, total: int) -> str:
    """Format a compact value/total pair."""
    return f"{value}/{total}"


def _format_delegations_summary(pending: int, active: int, total: int) -> str:
    """Format a compact async delegation summary string."""
    return f"{pending} pending / {active} active / {total} total"


def _gateway_active_agents(state: dict[str, object]) -> int:
    """Return active agent count from gateway state JSON."""
    value = state.get("active_agents")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _gateway_platform_summary(state: dict[str, object]) -> tuple[int, int, str]:
    """Return connected/total platform counts and a compact summary."""
    platforms_obj = state.get("platforms")
    if not isinstance(platforms_obj, dict):
        return 0, 0, ""

    connected = 0
    total = 0
    labels: list[str] = []
    for name, value in platforms_obj.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        total += 1
        platform_state = _mapping_str(value, "state").lower()
        if platform_state == "connected":
            connected += 1
        labels.append(f"{name}:{platform_state or 'unknown'}")

    summary = f"{connected}/{total} connected"
    if labels:
        summary = f"{summary} ({', '.join(labels[:3])})"
    return connected, total, summary


def _mapping_str(mapping: dict[str, object], key: str) -> str:
    """Return one mapping value as a stripped string."""
    value = mapping.get(key)
    if isinstance(value, str):
        return value.strip()
    return ""


def _resolve_provider_name(
    provider_id: str,
    base_url: str,
    model_name: str,
    custom_providers: object,
) -> str:
    """Resolve a human-readable provider name from Hermes config/session data."""
    provider_name = _provider_label_from_id(provider_id)
    if provider_id == "custom":
        custom_name = _resolve_custom_provider_name(custom_providers, model_name, base_url)
        if custom_name:
            return custom_name
    if provider_name:
        return provider_name
    return _provider_label_from_base_url(base_url)


def _resolve_custom_provider_name(
    custom_providers: object,
    model_name: str,
    base_url: str,
) -> str:
    """Return a provider name from Hermes custom provider definitions."""
    if not isinstance(custom_providers, list):
        return ""

    for provider in custom_providers:
        if not isinstance(provider, dict):
            continue
        provider_model = _mapping_str(provider, "model")
        provider_base_url = _mapping_str(provider, "base_url")
        models_obj = provider.get("models")
        model_matches = False
        if isinstance(models_obj, list):
            model_matches = any(item == model_name for item in models_obj if isinstance(item, str))
        if model_name and provider_model == model_name:
            model_matches = True

        if not model_matches and base_url and provider_base_url != base_url:
            continue
        if not model_matches and not base_url:
            continue

        name = _mapping_str(provider, "name")
        extracted = _extract_provider_name(name)
        if extracted:
            return extracted
        guessed = _provider_label_from_base_url(provider_base_url)
        if guessed:
            return guessed

    return ""


def _extract_provider_name(name: str) -> str:
    """Extract a concise provider label from a custom provider display name."""
    if not name:
        return ""
    start = name.find("(")
    end = name.find(")", start + 1)
    if start != -1 and end != -1:
        return name[start + 1:end].strip()
    return name.strip()


def _provider_label_from_id(provider_id: str) -> str:
    """Map Hermes provider IDs to human-readable provider labels."""
    labels = {
        "anthropic": "Anthropic",
        "bedrock": "Bedrock",
        "custom": "",
        "kimi-coding": "Kimi",
        "kimi-coding-cn": "Kimi CN",
        "minimax": "MiniMax",
        "minimax-cn": "MiniMax CN",
        "nous": "Nous",
        "ollama": "Ollama",
        "openai": "OpenAI",
        "openai-codex": "OpenAI Codex",
        "openrouter": "OpenRouter",
        "xai": "xAI",
        "xai-oauth": "xAI",
        "zai": "Z.AI",
    }
    return labels.get(provider_id.strip().lower(), "") if provider_id else ""


def _provider_label_from_base_url(base_url: str) -> str:
    """Infer a provider label from a base URL when possible."""
    if not base_url:
        return ""
    hostname = urlparse(base_url).hostname or ""
    lowered = hostname.lower()
    if "x.ai" in lowered:
        return "xAI"
    if "openrouter" in lowered:
        return "OpenRouter"
    if "anthropic" in lowered:
        return "Anthropic"
    if "openai" in lowered:
        return "OpenAI"
    return ""


def _format_model_display(provider_name: str, model_name: str, base_url: str = "") -> str:
    """Format a provider/model label for display."""
    cleaned_model = _display_model_name(model_name)
    if not cleaned_model:
        return ""

    mapped_provider = _provider_label_from_id(provider_name)
    inferred_provider = _provider_label_from_base_url(base_url)
    fallback_provider = provider_name.strip() if provider_name.strip().lower() != "custom" else ""
    resolved_provider = mapped_provider or inferred_provider or fallback_provider
    if not resolved_provider:
        return cleaned_model
    return f"{resolved_provider} / {cleaned_model}"


def _display_model_name(model_name: str) -> str:
    """Normalize a Hermes model name for compact display."""
    cleaned = model_name.strip()
    if cleaned.endswith(":latest"):
        return cleaned[: -len(":latest")]
    return cleaned


def _pid_exists(pid: int) -> bool:
    """Return whether a PID currently exists without sending a signal."""
    if pid <= 0:
        return False
    return Path(f"/proc/{pid}").exists()
