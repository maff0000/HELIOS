"""State model: legal transitions and direction semantics."""

from __future__ import annotations

import itertools

import pytest

from helios.contracts import (
    LEGAL_TRANSITIONS,
    LIVE_STATES,
    RESOLVED_STATES,
    Direction,
    StrategyState,
    is_legal_transition,
    legal_successors,
    transition,
)
from helios.errors import IllegalStateTransitionError
from tests.conftest import utc


def test_every_pid_state_exists():
    assert {member.value for member in StrategyState} == {
        "DORMANT", "FORMING", "MATCHED", "ACTIVE", "WEAKENING", "INVALID", "EXPIRED",
    }


def test_every_state_has_a_documented_transition_set():
    assert set(LEGAL_TRANSITIONS) == set(StrategyState)


def test_every_state_may_republish_itself():
    """HELIOS re-evaluates continuously; an unchanged state is still published."""
    for state in StrategyState:
        assert is_legal_transition(state, state)


def test_every_state_is_reachable_from_dormant():
    reachable = {StrategyState.DORMANT}
    frontier = [StrategyState.DORMANT]
    while frontier:
        current = frontier.pop()
        for successor in legal_successors(current):
            if successor not in reachable:
                reachable.add(successor)
                frontier.append(successor)
    assert reachable == set(StrategyState)


@pytest.mark.parametrize(
    ("current", "proposed"),
    [
        (StrategyState.DORMANT, StrategyState.FORMING),
        (StrategyState.DORMANT, StrategyState.MATCHED),
        (StrategyState.FORMING, StrategyState.MATCHED),
        (StrategyState.FORMING, StrategyState.DORMANT),
        (StrategyState.MATCHED, StrategyState.ACTIVE),
        (StrategyState.ACTIVE, StrategyState.WEAKENING),
        (StrategyState.WEAKENING, StrategyState.ACTIVE),
        (StrategyState.ACTIVE, StrategyState.INVALID),
        (StrategyState.ACTIVE, StrategyState.EXPIRED),
        (StrategyState.INVALID, StrategyState.DORMANT),
        (StrategyState.EXPIRED, StrategyState.DORMANT),
        (StrategyState.DORMANT, StrategyState.INVALID),
        (StrategyState.EXPIRED, StrategyState.INVALID),
    ],
)
def test_documented_transitions_are_legal(current, proposed):
    assert is_legal_transition(current, proposed)


@pytest.mark.parametrize(
    ("current", "proposed"),
    [
        (StrategyState.MATCHED, StrategyState.DORMANT),
        (StrategyState.MATCHED, StrategyState.FORMING),
        (StrategyState.ACTIVE, StrategyState.MATCHED),
        (StrategyState.ACTIVE, StrategyState.FORMING),
        (StrategyState.WEAKENING, StrategyState.MATCHED),
        (StrategyState.INVALID, StrategyState.ACTIVE),
        (StrategyState.EXPIRED, StrategyState.MATCHED),
        (StrategyState.DORMANT, StrategyState.ACTIVE),
        (StrategyState.DORMANT, StrategyState.WEAKENING),
    ],
)
def test_undocumented_transitions_fail_loudly(current, proposed):
    assert not is_legal_transition(current, proposed)
    with pytest.raises(IllegalStateTransitionError):
        transition(current, proposed, at_utc=utc("2026-01-05T00:00:00Z"), reason="test")


def test_a_matched_occurrence_must_resolve_before_rearming():
    """MATCHED/ACTIVE/WEAKENING may not fall back to DORMANT directly: the
    lifecycle stays auditable because every match resolves explicitly."""
    for state in LIVE_STATES:
        assert StrategyState.DORMANT not in legal_successors(state)
    for state in RESOLVED_STATES:
        assert StrategyState.DORMANT in legal_successors(state)


def test_resolved_states_rearm_only_through_dormant():
    """DORMANT is the only NON-resolved successor a resolved state has.

    Reaching the other resolved state is not rearming: it re-resolves. What the
    table must forbid is going straight back to FORMING/MATCHED/ACTIVE/
    WEAKENING without passing through DORMANT.
    """
    for state in RESOLVED_STATES:
        assert legal_successors(state) - RESOLVED_STATES == frozenset(
            {StrategyState.DORMANT}
        )
        assert state in legal_successors(state)


def test_every_state_can_become_invalid():
    """INVALID is the model's word for "this state is void".

    A strategy whose *evaluation itself* failed is void whatever it was doing
    beforehand, and the containment path in ``helios/strategies/evaluation.py``
    is validated against this table like every other transition. If any state
    could not reach INVALID, containment would have to route around the table
    — or raise out of the handler and take the failing strategy's siblings with
    it.
    """
    for state in StrategyState:
        assert is_legal_transition(state, StrategyState.INVALID), state


def test_transition_requires_a_reason():
    with pytest.raises(IllegalStateTransitionError):
        transition(
            StrategyState.DORMANT,
            StrategyState.MATCHED,
            at_utc=utc("2026-01-05T00:00:00Z"),
            reason="   ",
        )


def test_transition_records_what_happened():
    record = transition(
        StrategyState.DORMANT,
        StrategyState.MATCHED,
        at_utc=utc("2026-01-05T00:00:00Z"),
        reason="ema_50 crossed above ema_200",
    )
    assert record.changed
    assert record.previous is StrategyState.DORMANT
    assert record.current is StrategyState.MATCHED
    assert record.at_utc.tzinfo is not None
    with pytest.raises(Exception):
        record.current = StrategyState.ACTIVE  # type: ignore[misc]


def test_transitions_require_state_values():
    with pytest.raises(IllegalStateTransitionError):
        transition("DORMANT", "MATCHED", at_utc=utc("2026-01-05T00:00:00Z"), reason="x")  # type: ignore[arg-type]


def test_the_state_sets_partition_sensibly():
    assert LIVE_STATES == {
        StrategyState.MATCHED, StrategyState.ACTIVE, StrategyState.WEAKENING
    }
    assert RESOLVED_STATES == {StrategyState.INVALID, StrategyState.EXPIRED}
    assert not LIVE_STATES & RESOLVED_STATES


def test_direction_distinguishes_neutral_from_not_applicable():
    assert Direction.LONG.is_directional
    assert Direction.SHORT.is_directional
    assert not Direction.NEUTRAL.is_directional
    assert not Direction.NONE.is_directional
    assert Direction.NEUTRAL is not Direction.NONE


def test_direction_relationships():
    assert Direction.LONG.opposite is Direction.SHORT
    assert Direction.SHORT.opposite is Direction.LONG
    assert Direction.NONE.opposite is Direction.NONE
    assert Direction.LONG.agrees_with(Direction.LONG)
    assert not Direction.LONG.agrees_with(Direction.SHORT)
    assert Direction.LONG.opposes(Direction.SHORT)
    assert not Direction.NEUTRAL.opposes(Direction.LONG)
    for one, other in itertools.product(Direction, repeat=2):
        assert not (one.agrees_with(other) and one.opposes(other))
