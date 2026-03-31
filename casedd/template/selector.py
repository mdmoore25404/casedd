"""Runtime template selection policies.

Resolves the active template name on each render tick by applying policies in
priority order:

1. Trigger rules (metric-driven)
2. Schedule rules (time-of-day)
3. Rotation list
4. Base template from config

Public API:
    - :class:`TemplateSelector` — stateful policy evaluator
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time
import time as monotonic_time

from casedd.config import (
    RotationEntry,
    RotationSkipCondition,
    TemplateScheduleRule,
    TemplateTriggerRule,
)
from casedd.data_store import StoreValue


@dataclass(frozen=True)
class _IndexedTrigger:
    """Internal trigger record preserving config order with priority sorting."""

    index: int
    rule: TemplateTriggerRule


class TemplateSelector:
    """Select the active template according to runtime policies.

    Args:
        base_template: Default template name.
        rotation_templates: Additional templates to rotate through.
        rotation_interval: Seconds between rotation steps (default dwell).
        schedule_rules: Time-window rules.
        trigger_rules: Data-value trigger rules.
        force_store_key: Optional data-store key that overrides selection.
        rotation_entries: Full ordered rotation entry list. Overrides
            ``rotation_templates`` when provided.
    """

    def __init__(  # noqa: PLR0913 -- explicit policy args improve readability
        self,
        base_template: str,
        rotation_templates: list[str],
        rotation_interval: float,
        rotation_enabled: bool,
        schedule_rules: list[TemplateScheduleRule],
        trigger_rules: list[TemplateTriggerRule],
        force_store_key: str | None = None,
        rotation_entries: list[RotationEntry] | None = None,
        template_resolver: Callable[[str], list[RotationSkipCondition]] | None = None,
        on_trigger_activate: (
            Callable[[TemplateTriggerRule, StoreValue | None], None] | None
        ) = None,
    ) -> None:
        """Initialise the selector and state used across render ticks.

        Args:
            base_template: Default template name.
            rotation_templates: Additional templates to rotate through.
            rotation_interval: Seconds between rotation steps (default dwell).
            rotation_enabled: When false, rotation is disabled and selection
                falls back to ``base_template`` after trigger/schedule policy.
            schedule_rules: Time-window rules.
            trigger_rules: Data-value trigger rules.
            force_store_key: Optional data-store key that overrides selection.
            rotation_entries: Full ordered rotation entry list with per-entry
                dwell times and skip conditions. Overrides ``rotation_templates``.
            template_resolver: Callable that returns a template's built-in
                ``skip_if`` conditions by name.  Used as fallback when a
                rotation entry has no entry-level skip conditions.  Rotation-
                level conditions always take priority.
            on_trigger_activate: Optional callback invoked once when a trigger
                rule first activates.  Receives the rule and current store
                value (``None`` if the key was absent from the snapshot).
                Called synchronously on the render tick; keep it non-blocking
                (e.g. schedule an asyncio task and return).
        """
        self._base_template = base_template
        self._template_resolver = template_resolver
        self._on_trigger_activate = on_trigger_activate
        self._rotation_entries = self._build_rotation_entries(
            base_template, rotation_templates, rotation_entries
        )
        # Keep a flat template-name list for the legacy rotation_templates property.
        self._rotation_interval = rotation_interval
        self._rotation_enabled = rotation_enabled
        self._rotation_index = 0
        self._rotation_entry_start_ts = monotonic_time.monotonic()

        self._schedule_rules = schedule_rules
        indexed_triggers = [
            _IndexedTrigger(index=i, rule=rule)
            for i, rule in enumerate(trigger_rules)
            if not rule.disabled
        ]
        self._triggers = sorted(
            indexed_triggers,
            key=lambda item: (item.rule.priority, item.index),
        )
        self._trigger_true_since: dict[int, float] = {}
        self._trigger_active_since: dict[int, float] = {}
        self._trigger_cooldown_until: dict[int, float] = {}
        self._force_store_key = force_store_key

    def update_rotation(
        self,
        rotation_templates: list[str],
        rotation_interval: float,
        rotation_enabled: bool,
        entries: list[RotationEntry] | None = None,
    ) -> None:
        """Replace the rotation list and interval at runtime.

        Safe to call from any thread; Python's GIL guarantees atomic list
        replacement.  The rotation index is reset so the new list begins
        from the base template.

        Args:
            rotation_templates: New list of additional templates (legacy flat
                format, used when ``entries`` is not provided).
            rotation_interval: Default dwell in seconds between rotation steps.
            rotation_enabled: Enables/disables rotation logic.
            entries: Full ordered rotation entry list with per-entry dwell and
                skip conditions.  Overrides ``rotation_templates`` when given.
        """
        self._rotation_entries = self._build_rotation_entries(
            self._base_template, rotation_templates, entries
        )
        self._rotation_interval = max(1.0, rotation_interval)
        self._rotation_enabled = rotation_enabled
        self._rotation_index = 0
        self._rotation_entry_start_ts = monotonic_time.monotonic()

    @property
    def base_template(self) -> str:
        """The base (default) template name."""
        return self._base_template

    @property
    def rotation_entries(self) -> list[RotationEntry]:
        """Current ordered rotation entries."""
        return list(self._rotation_entries)

    @property
    def rotation_templates(self) -> list[str]:
        """Current configured rotation template names."""
        return [e.template for e in self._rotation_entries]

    @property
    def rotation_interval(self) -> float:
        """Default rotation dwell interval in seconds."""
        return self._rotation_interval

    @property
    def rotation_enabled(self) -> bool:
        """Whether rotation cycling is enabled for this selector."""
        return self._rotation_enabled

    @property
    def is_trigger_held(self) -> bool:
        """True when at least one trigger rule is currently active (holding a template).

        Used by the render loop to apply the alert border overlay.
        """
        return bool(self._trigger_active_since)

    def select_template(self, snapshot: dict[str, StoreValue]) -> str:
        """Return the active template name for the current tick.

        Args:
            snapshot: Current data-store snapshot.

        Returns:
            Selected template name.
        """
        if self._force_store_key is not None:
            forced_value = snapshot.get(self._force_store_key)
            if isinstance(forced_value, str):
                forced_name = forced_value.strip()
                if forced_name and forced_name.lower() != "auto":
                    return forced_name

        now_ts = monotonic_time.monotonic()
        now_dt = datetime.now(tz=UTC).astimezone()

        trigger_template = self._select_by_triggers(snapshot, now_ts)
        if trigger_template is not None:
            return trigger_template

        schedule_template = self._select_by_schedule(now_dt)
        if schedule_template is not None:
            return schedule_template

        return self._select_by_rotation(now_ts, snapshot)

    def _select_by_triggers(
        self,
        snapshot: dict[str, StoreValue],
        now_ts: float,
    ) -> str | None:
        """Evaluate trigger rules in priority order.

        Args:
            snapshot: Current data-store values.
            now_ts: Current monotonic timestamp.

        Returns:
            Template name if any trigger is active, else ``None``.
        """
        for item in self._triggers:
            idx = item.index
            rule = item.rule
            value = snapshot.get(rule.source)
            active_since = self._trigger_active_since.get(idx)

            if active_since is not None:
                hold_elapsed = now_ts >= active_since + rule.hold_for
                clear_match = self._trigger_clear_match(value, rule)
                if hold_elapsed and clear_match:
                    self._trigger_active_since.pop(idx, None)
                    self._trigger_true_since.pop(idx, None)
                    self._trigger_cooldown_until[idx] = now_ts + rule.cooldown
                    continue
                return rule.template

            if self._trigger_match(value, rule):
                if idx not in self._trigger_true_since:
                    self._trigger_true_since[idx] = now_ts

                true_since = self._trigger_true_since[idx]
                cooldown_until = self._trigger_cooldown_until.get(idx, 0.0)
                if now_ts >= true_since + rule.duration and now_ts >= cooldown_until:
                    self._trigger_active_since[idx] = now_ts
                    if self._on_trigger_activate is not None and rule.notify:
                        self._on_trigger_activate(rule, value)
                    return rule.template
            else:
                self._trigger_true_since.pop(idx, None)

        return None

    def _select_by_schedule(self, now_dt: datetime) -> str | None:
        """Find a matching scheduled template for the current local time.

        Args:
            now_dt: Current local datetime.

        Returns:
            Template name if a schedule matches, else ``None``.
        """
        now_weekday = now_dt.weekday()
        now_time = now_dt.time()
        for rule in self._schedule_rules:
            if rule.days and now_weekday not in rule.days:
                continue
            start = _parse_hhmm(rule.start)
            end = _parse_hhmm(rule.end)
            if _time_in_range(now_time, start, end):
                return rule.template
        return None

    def _select_by_rotation(self, now_ts: float, snapshot: dict[str, StoreValue]) -> str:
        """Rotate through configured entries using per-entry dwell and skip logic.

        Skip conditions are evaluated on every call; if the current entry
        becomes skippable mid-dwell, rotation advances immediately.  When all
        entries are skippable the current entry is held to avoid an empty state.

        Args:
            now_ts: Current monotonic timestamp.
            snapshot: Current data-store snapshot for skip evaluation.

        Returns:
            Template name of the active rotation entry.
        """
        if not self._rotation_enabled:
            return self._base_template

        if len(self._rotation_entries) == 1:
            return self._rotation_entries[0].template

        current = self._rotation_entries[self._rotation_index]

        # Immediately advance if the current entry is now skippable.
        if self._should_skip(current, snapshot):
            self._advance_rotation(now_ts, snapshot)
            return self._rotation_entries[self._rotation_index].template

        # Check whether the per-entry dwell time has elapsed.
        dwell = current.seconds if current.seconds is not None else self._rotation_interval
        if now_ts - self._rotation_entry_start_ts >= dwell:
            self._advance_rotation(now_ts, snapshot)

        return self._rotation_entries[self._rotation_index].template

    def _advance_rotation(self, now_ts: float, snapshot: dict[str, StoreValue]) -> None:
        """Advance to the next non-skipped entry, wrapping around as needed.

        If every entry is skippable the index stays on the current entry so
        the display always shows something.

        Args:
            now_ts: Current monotonic timestamp.
            snapshot: Current data-store snapshot for skip evaluation.
        """
        count = len(self._rotation_entries)
        original_index = self._rotation_index
        for _ in range(count):
            self._rotation_index = (self._rotation_index + 1) % count
            candidate = self._rotation_entries[self._rotation_index]
            if not self._should_skip(candidate, snapshot):
                self._rotation_entry_start_ts = now_ts
                return
        # All entries are skippable — hold on the original entry.
        self._rotation_index = original_index
        self._rotation_entry_start_ts = now_ts

    @staticmethod
    def _build_rotation_entries(
        base_template: str,
        rotation_templates: list[str],
        entries: list[RotationEntry] | None,
    ) -> list[RotationEntry]:
        """Build the ordered rotation entry list.

        When ``entries`` is provided and non-empty it is used directly.
        Otherwise an entry list is synthesised from the legacy flat
        ``rotation_templates`` list. If no rotation templates are configured,
        the base template is returned as the only entry.

        Args:
            base_template: Default template from config.
            rotation_templates: Legacy flat rotation list.
            entries: Full entry list with per-entry dwell/skip settings.

        Returns:
            Ordered, deduplicated list of :class:`RotationEntry` objects.
        """
        if entries:
            # Preserve configured order; deduplicate by template name.
            seen: set[str] = set()
            out: list[RotationEntry] = []
            for entry in entries:
                if entry.template in seen:
                    continue
                out.append(entry)
                seen.add(entry.template)
            return out

        # Legacy path: synthesise plain entries from a flat template-name list.
        ordered: list[RotationEntry] = []
        legacy_seen: set[str] = set()
        for name in rotation_templates:
            if name in legacy_seen:
                continue
            ordered.append(RotationEntry(template=name))
            legacy_seen.add(name)
        if ordered:
            return ordered
        return [RotationEntry(template=base_template)]

    def _should_skip(self, entry: RotationEntry, snapshot: dict[str, StoreValue]) -> bool:
        """Return True when all skip conditions on an entry are satisfied.

        Conditions are resolved in priority order:
        1. The rotation entry's own ``skip_if`` list (rotation-level, always wins).
        2. The template's built-in ``skip_if`` via the ``template_resolver``
           (template-level fallback used only when the entry has none).

        An empty condition list means the entry is never skipped.  A missing
        source key in the data store counts as a matched condition (i.e.,
        templates are skipped when their data has never arrived).

        Args:
            entry: Rotation entry to evaluate.
            snapshot: Current data-store snapshot.

        Returns:
            ``True`` when the entry should be skipped this tick.
        """
        # Rotation-level conditions take full priority.
        conditions = entry.skip_if
        # Fall back to template-level conditions when rotation entry has none.
        if not conditions and self._template_resolver is not None:
            conditions = self._template_resolver(entry.template)
        if not conditions:
            return False
        return all(self._skip_cond_match(cond, snapshot) for cond in conditions)

    @staticmethod
    def _skip_cond_match(cond: RotationSkipCondition, snapshot: dict[str, StoreValue]) -> bool:
        """Evaluate one skip condition against the current store snapshot.

        A missing key evaluates to ``True`` so that templates whose data has
        never arrived are hidden.

        Args:
            cond: Skip condition to evaluate.
            snapshot: Current data-store snapshot.

        Returns:
            ``True`` when the condition matches (i.e. the entry should be skipped).
        """
        value = snapshot.get(cond.source)
        if value is None:
            # Key not present → treat as satisfied (skip the template)
            return True
        value_num = _to_float(value)
        target_num = _to_float(cond.value)
        if value_num is not None and target_num is not None:
            return _compare(value_num, target_num, cond.operator)
        # Non-numeric fallback for eq/neq comparisons on string states.
        if cond.operator == "eq":
            return str(value) == str(cond.value)
        if cond.operator == "neq":
            return str(value) != str(cond.value)
        return False

    @staticmethod
    def _trigger_match(value: StoreValue | None, rule: TemplateTriggerRule) -> bool:
        """Evaluate one trigger condition against a current value.

        Args:
            value: Current store value for the trigger source key.
            rule: Trigger rule definition.

        Returns:
            ``True`` if the condition is met.
        """
        if value is None:
            return False

        op = rule.operator
        target = rule.value

        value_num = _to_float(value)
        target_num = _to_float(target)
        if value_num is not None and target_num is not None:
            return _compare(value_num, target_num, op)

        # Non-numeric fallback allows eq/neq triggering on string states.
        if op == "eq":
            return str(value) == str(target)
        if op == "neq":
            return str(value) != str(target)
        return False

    @staticmethod
    def _trigger_clear_match(value: StoreValue | None, rule: TemplateTriggerRule) -> bool:
        """Evaluate whether an active trigger is eligible to clear.

        Args:
            value: Current store value for the trigger source key.
            rule: Trigger rule definition.

        Returns:
            ``True`` when the active trigger should clear.
        """
        if rule.clear_operator is not None and rule.clear_value is not None:
            clear_rule = TemplateTriggerRule(
                source=rule.source,
                operator=rule.clear_operator,
                value=rule.clear_value,
                template=rule.template,
                duration=0.0,
                hold_for=0.0,
                cooldown=0.0,
                priority=rule.priority,
            )
            return TemplateSelector._trigger_match(value, clear_rule)

        # Default clear behavior: clear once the primary trigger condition no longer matches.
        return not TemplateSelector._trigger_match(value, rule)


def _to_float(value: StoreValue) -> float | None:
    """Convert supported store values to float when possible.

    Args:
        value: Candidate store value.

    Returns:
        Float value or ``None`` if conversion is invalid.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _compare(left: float, right: float, operator: str) -> bool:
    """Compare two numbers using the trigger operator string.

    Args:
        left: Current value.
        right: Trigger threshold value.
        operator: Operator token (gt, gte, lt, lte, eq, neq).

    Returns:
        Result of the comparison.
    """
    if operator == "gt":
        return left > right
    if operator == "gte":
        return left >= right
    if operator == "lt":
        return left < right
    if operator == "lte":
        return left <= right
    if operator == "eq":
        return left == right
    return left != right


def _parse_hhmm(value: str) -> time:
    """Parse a ``HH:MM`` time string.

    Args:
        value: Time string.

    Returns:
        Parsed ``datetime.time`` value.
    """
    hour_str, minute_str = value.split(":", maxsplit=1)
    return time(hour=int(hour_str), minute=int(minute_str))


def _time_in_range(now_value: time, start: time, end: time) -> bool:
    """Check if ``now`` falls inside a [start, end) local-time window.

    A range where ``start == end`` is treated as all day.

    Args:
        now_value: Current local wall-clock time.
        start: Range start.
        end: Range end.

    Returns:
        ``True`` when ``now`` is inside the window.
    """
    if start == end:
        return True
    if start < end:
        return start <= now_value < end
    # Overnight window, e.g. 22:00 -> 06:00
    return now_value >= start or now_value < end
