"""Persistence, expiry and the chain lifecycle over time.

A chain is not a one-shot predicate. It has to hold for a declared number of
evaluations before it publishes a match, stay valid for a declared window
afterwards, degrade when its components do, and resolve explicitly rather than
quietly falling back to DORMANT. Each of those is a rule with a test here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from helios.composition import ChainEngine
from helios.contracts.state import Direction, StrategyState
from helios.errors import StrategySpecError
from tests.test_chain_support import (
    INSTRUMENT,
    T0,
    at,
    atom,
    chain_package,
    component,
)


def pair(state=StrategyState.MATCHED, *, evaluated_at=None, matched_at=None,
         second_state=None, valid_until=None):
    """Two holding components, both LONG."""
    return [
        atom("golden_cross", state=state, evaluated_at=evaluated_at,
             matched_at=matched_at, valid_until=valid_until),
        atom("range_breakout", state=second_state or state,
             evaluated_at=evaluated_at, matched_at=matched_at),
    ]


def simple_chain(**kwargs):
    return chain_package(
        strategy_id="proof_temporal",
        primitive="ALL",
        components=[component("golden_cross"), component("range_breakout")],
        **kwargs,
    )


def run(engine, components, now, previous=None):
    return engine.evaluate(
        instrument=INSTRUMENT,
        evaluated_at_utc=now,
        components=components,
        previous=previous,
    )


# ------------------------------------------------------------- persistence


def test_a_chain_matches_only_after_the_declared_consecutive_evaluations():
    engine = ChainEngine(simple_chain(min_matched_frames=3))
    envelope = None
    states = []
    for step in range(4):
        moment = at(minutes=5 * step)
        envelope = run(
            engine,
            pair(evaluated_at=moment, matched_at=moment),
            moment,
            previous=envelope,
        )
        states.append(envelope.state)
    assert states == [
        StrategyState.FORMING,
        StrategyState.FORMING,
        StrategyState.MATCHED,
        StrategyState.ACTIVE,
    ]


def test_the_persistence_count_is_published_so_replay_needs_no_hidden_counter():
    engine = ChainEngine(simple_chain(min_matched_frames=3))
    first = run(engine, pair(), T0)
    assert first.evidence["satisfied_evaluations"] == 1
    assert first.evidence["min_matched_frames"] == 3
    second = run(engine, pair(), at(minutes=5), previous=first)
    assert second.evidence["satisfied_evaluations"] == 2


def test_a_broken_run_restarts_the_persistence_count():
    engine = ChainEngine(simple_chain(min_matched_frames=3))
    first = run(engine, pair(), T0)
    broken = run(
        engine,
        [atom("golden_cross"), atom("range_breakout", state=StrategyState.DORMANT)],
        at(minutes=5),
        previous=first,
    )
    assert broken.state is StrategyState.FORMING
    assert broken.evidence["satisfied_evaluations"] == 0
    resumed = run(engine, pair(), at(minutes=10), previous=broken)
    assert resumed.evidence["satisfied_evaluations"] == 1
    assert resumed.state is StrategyState.FORMING


# ------------------------------------------------------------------ expiry


def test_a_duration_expiry_publishes_a_validity_window_and_then_expires():
    engine = ChainEngine(
        simple_chain(expiry={"mode": "DURATION", "duration_seconds": 3600})
    )
    matched = run(engine, pair(), T0)
    assert matched.state is StrategyState.MATCHED
    assert matched.validity.valid_from_utc == T0
    assert matched.validity.valid_until_utc == at(hours=1)

    inside = run(engine, pair(evaluated_at=at(minutes=59)), at(minutes=59),
                 previous=matched)
    assert inside.state is StrategyState.ACTIVE

    outside = run(engine, pair(evaluated_at=at(seconds=3601)), at(seconds=3601),
                  previous=inside)
    assert outside.state is StrategyState.EXPIRED
    assert "time ran out rather than anything breaking" in outside.explanation


def test_a_frames_expiry_counts_frames_of_the_finest_timeframe_the_package_binds():
    package = chain_package(
        strategy_id="proof_frames_expiry",
        primitive="ALL",
        components=[
            component("golden_cross", role="CONTEXT"),
            component("range_breakout", role="TRIGGER"),
        ],
        role_timeframes={"CONTEXT": "H4", "TRIGGER": "M5"},
        expiry={"mode": "FRAMES", "frames": 6},
    )
    engine = ChainEngine(package)
    assert engine.expiry_horizon == timedelta(minutes=30)

    matched = run(
        engine,
        [
            atom("golden_cross", timeframe="H4", role="CONTEXT"),
            atom("range_breakout", timeframe="M5", role="TRIGGER"),
        ],
        T0,
    )
    assert matched.validity.valid_until_utc == at(minutes=30)
    later = at(minutes=31)
    expired = run(
        engine,
        [
            atom("golden_cross", timeframe="H4", role="CONTEXT", evaluated_at=later),
            atom("range_breakout", timeframe="M5", role="TRIGGER", evaluated_at=later),
        ],
        later,
        previous=matched,
    )
    assert expired.state is StrategyState.EXPIRED


def test_a_frames_expiry_with_no_bound_timeframe_is_refused_rather_than_guessed():
    package = simple_chain(expiry={"mode": "FRAMES", "frames": 6})
    with pytest.raises(StrategySpecError) as raised:
        ChainEngine(package)
    assert "frames of what" in str(raised.value)


def test_a_never_expiry_publishes_an_open_ended_validity():
    engine = ChainEngine(simple_chain(expiry={"mode": "NEVER"}))
    matched = run(engine, pair(), T0)
    assert matched.validity.valid_until_utc is None
    far = at(hours=48)
    still = run(engine, pair(evaluated_at=far), far, previous=matched)
    assert still.state is StrategyState.ACTIVE


def test_expiry_is_checked_before_the_condition_so_time_running_out_is_not_reported_as_breakage():
    engine = ChainEngine(
        simple_chain(expiry={"mode": "DURATION", "duration_seconds": 600})
    )
    matched = run(engine, pair(), T0)
    later = at(seconds=601)
    envelope = run(
        engine,
        [
            atom("golden_cross", evaluated_at=later),
            atom("range_breakout", state=StrategyState.DORMANT, evaluated_at=later),
        ],
        later,
        previous=matched,
    )
    assert envelope.state is StrategyState.EXPIRED


# ------------------------------------------------- component persistence


def test_a_component_match_counts_for_exactly_as_long_as_the_component_says():
    engine = ChainEngine(simple_chain())
    matched = run(engine, pair(valid_until=at(minutes=10)), T0)
    assert matched.state is StrategyState.MATCHED

    inside = run(
        engine,
        pair(evaluated_at=at(minutes=9), matched_at=T0, valid_until=at(minutes=10)),
        at(minutes=9),
        previous=matched,
    )
    assert inside.state is StrategyState.ACTIVE

    outside = run(
        engine,
        pair(evaluated_at=at(minutes=11), matched_at=T0, valid_until=at(minutes=10)),
        at(minutes=11),
        previous=inside,
    )
    assert outside.state is StrategyState.INVALID
    assert "COMPONENT_VALIDITY_LAPSED" in outside.explanation


# --------------------------------------------------------------- weakening


def test_a_chain_weakens_when_a_contributing_component_weakens_and_recovers():
    engine = ChainEngine(simple_chain(weakening_enabled=True))
    matched = run(engine, pair(), T0)

    weak_at = at(minutes=5)
    weakening = run(
        engine,
        [
            atom("golden_cross", evaluated_at=weak_at, matched_at=T0),
            atom("range_breakout", state=StrategyState.WEAKENING,
                 evaluated_at=weak_at, matched_at=T0),
        ],
        weak_at,
        previous=matched,
    )
    assert weakening.state is StrategyState.WEAKENING
    assert "reports WEAKENING" in weakening.explanation

    back_at = at(minutes=10)
    recovered = run(
        engine,
        [
            atom("golden_cross", evaluated_at=back_at, matched_at=T0),
            atom("range_breakout", state=StrategyState.ACTIVE,
                 evaluated_at=back_at, matched_at=T0),
        ],
        back_at,
        previous=weakening,
    )
    assert recovered.state is StrategyState.ACTIVE
    # One continuous activation across the excursion.
    assert recovered.active_since_utc == matched.active_since_utc == T0
    assert recovered.first_matched_at_utc == T0


def test_an_any_chain_weakens_when_fewer_components_hold():
    engine = ChainEngine(
        chain_package(
            strategy_id="proof_any_weakening",
            primitive="ANY",
            components=[component("golden_cross"), component("range_breakout")],
        )
    )
    matched = run(engine, pair(), T0)
    assert matched.strength == 1

    later = at(minutes=5)
    weaker = run(
        engine,
        [
            atom("golden_cross", evaluated_at=later, matched_at=T0),
            atom("range_breakout", state=StrategyState.DORMANT, evaluated_at=later),
        ],
        later,
        previous=matched,
    )
    assert weaker.state is StrategyState.WEAKENING
    assert str(weaker.strength) == "0.5000"
    assert "fewer components do" in weaker.explanation


def test_weakening_is_not_published_when_the_package_disables_it():
    engine = ChainEngine(simple_chain(weakening_enabled=False))
    matched = run(engine, pair(), T0)
    later = at(minutes=5)
    envelope = run(
        engine,
        [
            atom("golden_cross", evaluated_at=later, matched_at=T0),
            atom("range_breakout", state=StrategyState.WEAKENING,
                 evaluated_at=later, matched_at=T0),
        ],
        later,
        previous=matched,
    )
    assert envelope.state is StrategyState.ACTIVE


# ------------------------------------------------------ resolution and rearm


def test_a_resolved_occurrence_latches_until_the_condition_clears_then_rearms():
    engine = ChainEngine(
        simple_chain(expiry={"mode": "DURATION", "duration_seconds": 600})
    )
    matched = run(engine, pair(), T0)

    expired_at = at(seconds=601)
    expired = run(engine, pair(evaluated_at=expired_at), expired_at, previous=matched)
    assert expired.state is StrategyState.EXPIRED

    # The same evidence must not re-fire the chain.
    latched_at = at(seconds=602)
    latched = run(engine, pair(evaluated_at=latched_at), latched_at, previous=expired)
    assert latched.state is StrategyState.EXPIRED
    assert "rearms once the composed condition clears" in latched.explanation
    assert latched.validity == expired.validity

    cleared_at = at(seconds=700)
    cleared = run(
        engine,
        [
            atom("golden_cross", state=StrategyState.DORMANT, evaluated_at=cleared_at),
            atom("range_breakout", state=StrategyState.DORMANT,
                 evaluated_at=cleared_at),
        ],
        cleared_at,
        previous=latched,
    )
    assert cleared.state is StrategyState.DORMANT
    assert cleared.first_matched_at_utc is None

    fresh_at = at(seconds=800)
    rearmed = run(
        engine,
        pair(evaluated_at=fresh_at, matched_at=fresh_at),
        fresh_at,
        previous=cleared,
    )
    assert rearmed.state is StrategyState.MATCHED
    assert rearmed.first_matched_at_utc == fresh_at


def test_a_live_chain_whose_condition_breaks_is_invalid_not_dormant():
    engine = ChainEngine(simple_chain())
    matched = run(engine, pair(), T0)
    later = at(minutes=5)
    envelope = run(
        engine,
        [
            atom("golden_cross", evaluated_at=later, matched_at=T0),
            atom("range_breakout", state=StrategyState.DORMANT, evaluated_at=later),
        ],
        later,
        previous=matched,
    )
    assert envelope.state is StrategyState.INVALID
    assert envelope.validity.reason is not None
    assert envelope.first_matched_at_utc == T0
    assert envelope.active_since_utc is None


def test_an_invalidated_component_voids_a_live_chain_with_the_component_reason():
    engine = ChainEngine(simple_chain())
    matched = run(engine, pair(), T0)
    later = at(minutes=5)
    envelope = run(
        engine,
        [
            atom("golden_cross", evaluated_at=later, matched_at=T0),
            atom("range_breakout", state=StrategyState.INVALID, evaluated_at=later,
                 matched_at=T0, validity_reason="its own condition was voided"),
        ],
        later,
        previous=matched,
    )
    assert envelope.state is StrategyState.INVALID
    assert "COMPONENT_INVALIDATED" in envelope.explanation
    assert "its own condition was voided" in envelope.explanation


def test_a_component_that_aged_out_is_reported_distinctly_from_one_that_broke():
    engine = ChainEngine(simple_chain())
    envelope = run(
        engine,
        [
            atom("golden_cross"),
            atom("range_breakout", state=StrategyState.EXPIRED,
                 validity_reason="its declared window ended"),
        ],
        T0,
    )
    assert envelope.state is StrategyState.FORMING
    assert "COMPONENT_AGED_OUT" in envelope.explanation


def test_direction_is_still_published_on_a_resolved_chain():
    engine = ChainEngine(simple_chain())
    matched = run(engine, pair(), T0)
    later = at(minutes=5)
    voided = run(
        engine,
        [
            atom("golden_cross", evaluated_at=later, matched_at=T0),
            atom("range_breakout", state=StrategyState.DORMANT, evaluated_at=later),
        ],
        later,
        previous=matched,
    )
    assert voided.direction is Direction.LONG
