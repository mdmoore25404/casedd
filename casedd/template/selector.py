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

from dataclasses import dataclass
from datetime import UTC, datetime, time
import time as monotonic_time

from casedd.config import TemplateScheduleRule, TemplateTriggerRule
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
        rotation_interval: Seconds between rotation steps.
        schedule_rules: Time-window rules.
        trigger_rules: Data-value trigger rules.
    """

    def __init__(
        self,
        base_template: str,
        rotation_templates: list[str],
        rotation_interval: float,
        schedule_rules: list[TemplateScheduleRule],
        trigger_rules: list[TemplateTriggerRule],
    ) -> None:
        """Initialise the selector and state used across render ticks.

        Args:
            base_template: Default template name.
            rotation_templates: Additional templates to rotate through.
            rotation_interval: Seconds between rotation steps.
            schedule_rules: Time-window rules.
            trigger_rules: Data-value trigger rules.
        """
        self._base_template = base_template
        self._rotation_templates = self._build_rotation_list(base_template, rotation_templates)
        self._rotation_interval = rotation_interval
        self._rotation_index = 0
        self._rotation_last_step = monotonic_time.monotonic()

        self._schedule_rules = schedule_rules
        indexed_triggers = [
            _IndexedTrigger(index=i, rule=rule) for i, rule in enumerate(trigger_rules)
        ]
        self._triggers = sorted(
            indexed_triggers,
            key=lambda item: (item.rule.priority, item.index),
        )
        self._trigger_true_since: dict[int, float] = {}
        self._trigger_active_since: dict[int, float] = {}
        self._trigger_cooldown_until: dict[int, float] = {}

    def select_template(self, snapshot: dict[str, StoreValue]) -> str:
        """Return the active template name for the current tick.

        Args:
            snapshot: Current data-store snapshot.

        Returns:
            Selected template name.
        """
        now_ts = monotonic_time.monotonic()
        now_dt = datetime.now(tz=UTC).astimezone()

        trigger_template = self._select_by_triggers(snapshot, now_ts)
        if trigger_template is not None:
            return trigger_template

        schedule_template = self._select_by_schedule(now_dt)
        if schedule_template is not None:
            return schedule_template

        return self._select_by_rotation(now_ts)

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

    def _select_by_rotation(self, now_ts: float) -> str:
        """Rotate through configured templates at a fixed interval.

        Args:
            now_ts: Current monotonic timestamp.

        Returns:
            Rotated template name (or base template when rotation is disabled).
        """
        if len(self._rotation_templates) == 1:
            return self._rotation_templates[0]

        elapsed = now_ts - self._rotation_last_step
        if elapsed >= self._rotation_interval:
            steps = int(elapsed // self._rotation_interval)
            self._rotation_index = (self._rotation_index + steps) % len(self._rotation_templates)
            self._rotation_last_step += steps * self._rotation_interval

        return self._rotation_templates[self._rotation_index]

    @staticmethod
    def _build_rotation_list(base_template: str, templates: list[str]) -> list[str]:
        """Build an ordered unique list containing base + rotation templates.

        Args:
            base_template: Default template from config.
            templates: User-provided rotation list.

        Returns:
            Ordered unique template list.
        """
        ordered: list[str] = [base_template]
        seen: set[str] = {base_template}
        for item in templates:
            if item in seen:
                continue
            ordered.append(item)
            seen.add(item)
        return ordered

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
