"""Tests for template rotation behavior in :mod:`casedd.template.selector`."""

from __future__ import annotations

import logging

from casedd.config import RotationEntry, RotationSkipCondition
from casedd.template.selector import TemplateSelector


def test_rotation_entries_exclude_base_when_rotation_configured() -> None:
    """Configured rotation templates are used as-is without auto-prepending base."""
    selector = TemplateSelector(
        base_template="system_stats",
        rotation_templates=["apod", "nzbget_queue"],
        rotation_interval=30.0,
        rotation_enabled=True,
        schedule_rules=[],
        trigger_rules=[],
    )

    templates = [entry.template for entry in selector.rotation_entries]
    assert templates == ["apod", "nzbget_queue"]


def test_rotation_disabled_falls_back_to_base_template() -> None:
    """When rotation is disabled, selector returns base template."""
    selector = TemplateSelector(
        base_template="system_stats",
        rotation_templates=["apod", "nzbget_queue"],
        rotation_interval=30.0,
        rotation_enabled=False,
        schedule_rules=[],
        trigger_rules=[],
    )

    chosen = selector.select_template({})
    assert chosen == "system_stats"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(template: str, **cond: object) -> RotationEntry:
    """Build a RotationEntry with one skip condition for test brevity."""
    return RotationEntry(
        template=template,
        skip_if=[RotationSkipCondition(**cond)],  # type: ignore[arg-type]
    )


def _selector_with_entries(entries: list[RotationEntry]) -> TemplateSelector:
    return TemplateSelector(
        base_template=entries[0].template,
        rotation_templates=[e.template for e in entries],
        rotation_interval=30.0,
        rotation_enabled=True,
        schedule_rules=[],
        trigger_rules=[],
        rotation_entries=entries,
    )


# ---------------------------------------------------------------------------
# Rotation-level skip_if tests
# ---------------------------------------------------------------------------


class TestRotationLevelSkipIf:
    """Rotation-level skip_if: explicitly set by user in casedd.yaml."""

    def test_eq_condition_matches_skips_entry(self) -> None:
        """Entry is skipped when eq condition is true."""
        entry = _make_entry(
            "os_updates", source="os_updates.total_count", operator="eq", value=0
        )
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])
        chosen = sel.select_template({"os_updates.total_count": 0.0})
        assert chosen == "system_stats"

    def test_eq_condition_no_match_shows_entry(self) -> None:
        """Entry is shown when eq condition is false."""
        entry = _make_entry(
            "os_updates", source="os_updates.total_count", operator="eq", value=0
        )
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])
        chosen = sel.select_template({"os_updates.total_count": 3.0})
        assert chosen == "os_updates"

    def test_impossible_condition_never_skips(self) -> None:
        """eq -1 is never true for a non-negative counter; template always shows."""
        entry = _make_entry(
            "os_updates",
            source="os_updates.actionable_count",
            operator="eq",
            value=-1,
        )
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])
        chosen = sel.select_template({"os_updates.actionable_count": 0.0})
        assert chosen == "os_updates"

    def test_missing_key_does_not_auto_skip_rotation_level(self) -> None:
        """Rotation-level skip_if with missing key: should NOT auto-skip.

        The user controls skip behaviour via the condition.  A missing key must
        not be treated as "condition matched" for rotation-level entries —
        otherwise impossible conditions like ``eq -1`` (meant to force-show) are
        ignored.
        """
        entry = _make_entry(
            "os_updates",
            source="os_updates.actionable_count",
            operator="eq",
            value=-1,
        )
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])
        # No os_updates keys in store (getter not yet published)
        chosen = sel.select_template({})
        assert chosen == "os_updates"

    def test_missing_key_eq_zero_does_not_auto_skip(self) -> None:
        """Rotation-level eq 0 with missing key: key absent → condition False → show.

        Template briefly visible until getter publishes real value, which is
        then re-evaluated each frame.
        """
        entry = _make_entry(
            "os_updates",
            source="os_updates.actionable_count",
            operator="eq",
            value=0,
        )
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])
        chosen = sel.select_template({})
        assert chosen == "os_updates"

    def test_gt_condition_skips(self) -> None:
        """gt operator: skip when value exceeds threshold."""
        entry = _make_entry("heavy", source="cpu.percent", operator="gt", value=90)
        sel = _selector_with_entries([entry, RotationEntry(template="idle")])
        assert sel.select_template({"cpu.percent": 95.0}) == "idle"

    def test_lte_condition_shows(self) -> None:
        """lte operator: show when value does not satisfy condition."""
        entry = _make_entry("gpu_detail", source="nvidia.percent", operator="lte", value=20)
        sel = _selector_with_entries([entry, RotationEntry(template="other")])
        # 50 lte 20 is False → don't skip
        chosen = sel.select_template({"nvidia.percent": 50.0})
        assert chosen == "gpu_detail"

    def test_neq_string_condition(self) -> None:
        """neq on string values: skip when strings differ."""
        entry = _make_entry("ups", source="ups.status", operator="neq", value="ONLINE")
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])
        # "ONLINE" neq "ONLINE" is False → don't skip
        assert sel.select_template({"ups.status": "ONLINE"}) == "ups"

    def test_empty_skip_if_never_skips(self) -> None:
        """An entry with skip_if=[] is never skipped."""
        entry = RotationEntry(template="system_stats", skip_if=[])
        sel = _selector_with_entries([entry])
        assert sel.select_template({}) == "system_stats"

    def test_skip_log_contains_reason(self, caplog: object) -> None:
        """When an entry is skipped, selector logs the matched skip reason."""
        entry = _make_entry(
            "os_updates",
            source="os_updates.actionable_count",
            operator="eq",
            value=0,
        )
        sel = _selector_with_entries([entry, RotationEntry(template="system_stats")])

        with caplog.at_level(logging.DEBUG, logger="casedd.template.selector"):
            chosen = sel.select_template({"os_updates.actionable_count": 0.0})

        assert chosen == "system_stats"
        assert "Rotation entry skipped:" in caplog.text
        assert "os_updates.actionable_count=0.0 op=eq target=0.0" in caplog.text


# ---------------------------------------------------------------------------
# Template-level skip_if fallback tests
# ---------------------------------------------------------------------------


class TestTemplateLevelSkipIf:
    """Template-level skip_if: resolved from template registry as fallback."""

    def test_missing_key_auto_skips_via_template_fallback(self) -> None:
        """Template-level fallback: missing key auto-skips (old behaviour preserved)."""
        entry = RotationEntry(template="os_updates", skip_if=[])
        sel = TemplateSelector(
            base_template="system_stats",
            rotation_templates=["os_updates", "system_stats"],
            rotation_interval=30.0,
            rotation_enabled=True,
            schedule_rules=[],
            trigger_rules=[],
            rotation_entries=[entry, RotationEntry(template="system_stats")],
            template_resolver=lambda name: (
                [
                    RotationSkipCondition(
                        source="os_updates.total_count", operator="eq", value=0
                    )
                ]
                if name == "os_updates"
                else []
            ),
        )
        # Key missing → template-level fallback treats absent key as matched → skip
        chosen = sel.select_template({})
        assert chosen == "system_stats"

    def test_template_level_shows_when_condition_false(self) -> None:
        """Template-level fallback: key present and condition false → show."""
        entry = RotationEntry(template="os_updates", skip_if=[])
        sel = TemplateSelector(
            base_template="system_stats",
            rotation_templates=["os_updates", "system_stats"],
            rotation_interval=30.0,
            rotation_enabled=True,
            schedule_rules=[],
            trigger_rules=[],
            rotation_entries=[entry, RotationEntry(template="system_stats")],
            template_resolver=lambda name: (
                [
                    RotationSkipCondition(
                        source="os_updates.total_count", operator="eq", value=0
                    )
                ]
                if name == "os_updates"
                else []
            ),
        )
        # total_count=3 → condition eq 0 is False → show os_updates
        chosen = sel.select_template({"os_updates.total_count": 3.0})
        assert chosen == "os_updates"


# ---------------------------------------------------------------------------
# _skip_cond_match unit tests
# ---------------------------------------------------------------------------


class TestSkipCondMatch:
    """Unit tests for the _skip_cond_match static method."""

    def _match(
        self,
        operator: str,
        value: object,
        store_value: object | None,
        *,
        missing_skips: bool = True,
    ) -> bool:
        cond = RotationSkipCondition(
            source="k",
            operator=operator,  # type: ignore[arg-type]
            value=value,  # type: ignore[arg-type]
        )
        snapshot: dict[str, object] = {} if store_value is None else {"k": store_value}
        return TemplateSelector._skip_cond_match(cond, snapshot, missing_skips=missing_skips)

    def test_missing_key_missing_skips_true(self) -> None:
        assert self._match("eq", 0, None, missing_skips=True) is True

    def test_missing_key_missing_skips_false(self) -> None:
        assert self._match("eq", 0, None, missing_skips=False) is False

    def test_eq_numeric_match(self) -> None:
        assert self._match("eq", 0, 0.0) is True

    def test_eq_numeric_no_match(self) -> None:
        assert self._match("eq", -1, 0.0) is False

    def test_gt_numeric_match(self) -> None:
        assert self._match("gt", 10, 15.0) is True

    def test_gt_numeric_no_match(self) -> None:
        assert self._match("gt", 10, 5.0) is False

    def test_lte_numeric_match(self) -> None:
        assert self._match("lte", 20, 20.0) is True

    def test_lte_numeric_no_match(self) -> None:
        assert self._match("lte", 20, 21.0) is False

    def test_eq_string_match(self) -> None:
        assert self._match("eq", "ONLINE", "ONLINE") is True

    def test_neq_string_skip(self) -> None:
        assert self._match("neq", "ONLINE", "BATTERY") is True

    def test_neq_string_no_skip(self) -> None:
        assert self._match("neq", "ONLINE", "ONLINE") is False
