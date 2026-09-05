"""Lifecycle timestamps derived deterministically from state."""

from __future__ import annotations

from datetime import timedelta

from helios.contracts import LifecycleTimestamps, StrategyState, advance_lifecycle
from tests.conftest import utc

START = utc("2026-01-05T00:00:00Z")


def at(hours: int):
    return START + timedelta(hours=hours)


def test_dormant_evaluation_publishes_only_the_evaluation_instant():
    lifecycle = advance_lifecycle(None, StrategyState.DORMANT, at(0))
    assert lifecycle.last_evaluated_at_utc == at(0)
    assert lifecycle.first_matched_at_utc is None
    assert lifecycle.last_matched_at_utc is None
    assert lifecycle.active_since_utc is None


def test_first_match_sets_every_match_instant():
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    assert lifecycle.first_matched_at_utc == at(1)
    assert lifecycle.last_matched_at_utc == at(1)
    assert lifecycle.active_since_utc == at(1)


def test_first_matched_is_preserved_while_last_matched_advances():
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.ACTIVE, at(2))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.ACTIVE, at(3))
    assert lifecycle.first_matched_at_utc == at(1)
    assert lifecycle.last_matched_at_utc == at(3)
    assert lifecycle.active_since_utc == at(1)


def test_weakening_does_not_restart_the_activation():
    """ACTIVE -> WEAKENING -> ACTIVE is one continuous activation."""
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.ACTIVE, at(2))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.WEAKENING, at(3))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.ACTIVE, at(4))
    assert lifecycle.active_since_utc == at(1)
    assert lifecycle.last_matched_at_utc == at(4)


def test_resolution_retains_history_but_ends_the_activation():
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.ACTIVE, at(2))
    resolved = advance_lifecycle(lifecycle, StrategyState.EXPIRED, at(3))
    assert resolved.first_matched_at_utc == at(1)
    assert resolved.last_matched_at_utc == at(2)
    assert resolved.active_since_utc is None
    assert resolved.last_evaluated_at_utc == at(3)


def test_returning_to_dormant_starts_a_clean_occurrence():
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.INVALID, at(2))
    lifecycle = advance_lifecycle(lifecycle, StrategyState.DORMANT, at(3))
    assert lifecycle.first_matched_at_utc is None
    assert lifecycle.last_matched_at_utc is None
    lifecycle = advance_lifecycle(lifecycle, StrategyState.MATCHED, at(4))
    assert lifecycle.first_matched_at_utc == at(4)


def test_forming_is_not_a_live_state():
    lifecycle = advance_lifecycle(None, StrategyState.FORMING, at(1))
    assert lifecycle.last_matched_at_utc is None
    assert lifecycle.active_since_utc is None
    assert lifecycle.last_evaluated_at_utc == at(1)


def test_lifecycle_is_deterministic_and_immutable():
    first = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    second = advance_lifecycle(None, StrategyState.MATCHED, at(1))
    assert first == second
    try:
        first.active_since_utc = at(5)  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("LifecycleTimestamps must be immutable")


def test_lifecycle_rejects_naive_instants():
    import datetime as stdlib_datetime

    try:
        LifecycleTimestamps(last_evaluated_at_utc=stdlib_datetime.datetime(2026, 1, 5))
    except Exception:
        return
    raise AssertionError("a naive instant must be refused")
