"""The four canonical composition primitives.

Every chain here is built from synthetic component envelopes, which is the
point: the engine composes the normalised output contract and nothing else.
"""

from __future__ import annotations

import pytest

from helios.composition import ChainEngine, ComponentReason
from helios.contracts.state import Direction, StrategyState
from tests.test_chain_support import (
    INSTRUMENT,
    T0,
    at,
    atom,
    chain_package,
    component,
)


def evaluate(package, components, *, now=None, previous=None):
    return ChainEngine(package).evaluate(
        instrument=INSTRUMENT,
        evaluated_at_utc=now or T0,
        components=components,
        previous=previous,
    )


# ------------------------------------------------------------------------ ALL


def all_chain(**kwargs):
    return chain_package(
        strategy_id="proof_all",
        primitive="ALL",
        components=[
            component("golden_cross"),
            component("range_breakout"),
            component("rejection_wick"),
        ],
        **kwargs,
    )


def test_all_matches_only_when_every_component_holds():
    envelope = evaluate(
        all_chain(),
        [atom("golden_cross"), atom("range_breakout"), atom("rejection_wick")],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG
    assert envelope.strength == 1


def test_all_does_not_match_when_one_component_is_absent_from_the_condition():
    envelope = evaluate(
        all_chain(),
        [
            atom("golden_cross"),
            atom("range_breakout"),
            atom("rejection_wick", state=StrategyState.DORMANT),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "rejection_wick@1.0.0" in envelope.explanation
    assert "STATE_NOT_REQUIRED" in envelope.explanation
    assert envelope.evidence["components_satisfied"] == 2


def test_all_is_dormant_when_nothing_holds():
    envelope = evaluate(
        all_chain(),
        [
            atom("golden_cross", state=StrategyState.DORMANT),
            atom("range_breakout", state=StrategyState.DORMANT),
            atom("rejection_wick", state=StrategyState.DORMANT),
        ],
    )
    assert envelope.state is StrategyState.DORMANT
    assert envelope.strength == 0


# ------------------------------------------------------------------------ ANY


def any_chain(**kwargs):
    return chain_package(
        strategy_id="proof_any",
        primitive="ANY",
        components=[component("golden_cross"), component("range_breakout")],
        **kwargs,
    )


def test_any_matches_on_a_single_holding_component():
    envelope = evaluate(
        any_chain(),
        [atom("golden_cross"), atom("range_breakout", state=StrategyState.DORMANT)],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.strength == pytest.approx(0.5) or str(envelope.strength) == "0.5000"


def test_any_still_explains_the_component_that_did_not_hold():
    envelope = evaluate(
        any_chain(),
        [atom("golden_cross"), atom("range_breakout", state=StrategyState.DORMANT)],
    )
    contributions = {
        str(item.strategy_id): item.contribution for item in envelope.components
    }
    assert contributions["golden_cross"].startswith("SATISFIED")
    assert contributions["range_breakout"].startswith("STATE_NOT_REQUIRED")


def test_any_is_dormant_when_no_component_holds():
    envelope = evaluate(
        any_chain(),
        [
            atom("golden_cross", state=StrategyState.DORMANT),
            atom("range_breakout", state=StrategyState.DORMANT),
        ],
    )
    assert envelope.state is StrategyState.DORMANT
    assert "did not match" in envelope.explanation


# ------------------------------------------------------------------- SEQUENCE


def sequence_chain(*, window: int = 28800, **kwargs):
    return chain_package(
        strategy_id="proof_sequence",
        primitive="SEQUENCE",
        components=[
            component("golden_cross", role="CONTEXT", sequence_index=0),
            component("swing_proximity", role="LOCATION", sequence_index=1),
            component("range_breakout", role="TRIGGER", sequence_index=2,
                      required_states=("MATCHED",)),
        ],
        role_timeframes={"CONTEXT": "H4", "LOCATION": "H1", "TRIGGER": "M5"},
        ordering_window_seconds=window,
        **kwargs,
    )


def ordered_components(first, second, third):
    return [
        atom("golden_cross", timeframe="H4", role="CONTEXT",
             state=StrategyState.ACTIVE, matched_at=first),
        atom("swing_proximity", timeframe="H1", role="LOCATION",
             state=StrategyState.ACTIVE, matched_at=second),
        atom("range_breakout", timeframe="M5", role="TRIGGER",
             state=StrategyState.MATCHED, matched_at=third),
    ]


def test_sequence_matches_when_components_matched_in_the_declared_order():
    envelope = evaluate(
        sequence_chain(),
        ordered_components(at(hours=-4), at(hours=-2), at(minutes=-5)),
    )
    assert envelope.state is StrategyState.MATCHED
    assert "matched in the declared order" in envelope.explanation


def test_sequence_does_not_match_when_a_later_component_matched_first():
    envelope = evaluate(
        sequence_chain(),
        ordered_components(at(hours=-1), at(hours=-2), at(minutes=-5)),
    )
    assert envelope.state is StrategyState.FORMING
    assert "OUT_OF_DECLARED_ORDER" in envelope.explanation
    assert "did not match in their declared order" in envelope.explanation


def test_sequence_treats_identical_match_instants_as_in_order():
    """A 4H close is also a 5M close; simultaneity is ordinary, not a breach."""
    same = at(hours=-1)
    envelope = evaluate(sequence_chain(), ordered_components(same, same, same))
    assert envelope.state is StrategyState.MATCHED


def test_sequence_does_not_match_outside_its_declared_ordering_window():
    envelope = evaluate(
        sequence_chain(window=3600),
        ordered_components(at(hours=-4), at(hours=-2), at(minutes=-5)),
    )
    assert envelope.state is StrategyState.FORMING
    assert "exceeds the declared ordering window of 3600s" in envelope.explanation
    # Each component still holds on its own terms; the combination does not fit.
    reasons = {item.contribution.split(":")[0] for item in envelope.components}
    assert reasons == {"SATISFIED"}
    assert envelope.evidence["components_satisfied"] == 3


def test_a_span_exactly_equal_to_the_ordering_window_is_inside_it():
    envelope = evaluate(
        sequence_chain(window=3600),
        ordered_components(at(hours=-1), at(minutes=-30), T0),
    )
    assert envelope.state is StrategyState.MATCHED


def test_sequence_needs_a_match_instant_to_establish_order():
    """A component satisfying on a non-live state publishes no match instant."""
    package = chain_package(
        strategy_id="proof_forming_sequence",
        primitive="SEQUENCE",
        components=[
            component("golden_cross", sequence_index=0),
            component("range_breakout", sequence_index=1,
                      required_states=("FORMING",)),
        ],
        ordering_window_seconds=3600,
    )
    envelope = evaluate(
        package,
        [
            atom("golden_cross", matched_at=at(hours=-1)),
            atom("range_breakout", state=StrategyState.FORMING,
                 direction=Direction.LONG),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "NO_MATCH_INSTANT" in envelope.explanation


# ------------------------------------------------------------ CONTEXT_TRIGGER


def context_trigger_chain(*, role_timeframes=None, **kwargs):
    return chain_package(
        strategy_id="proof_context_trigger",
        primitive="CONTEXT_TRIGGER",
        components=[
            component("golden_cross", role="CONTEXT"),
            component("range_breakout", role="TRIGGER",
                      required_states=("MATCHED",)),
        ],
        role_timeframes=role_timeframes or {"CONTEXT": "H4", "TRIGGER": "M5"},
        **kwargs,
    )


def context_and_trigger(*, context_at, trigger_at, context_until=None):
    return [
        atom("golden_cross", timeframe="H4", role="CONTEXT",
             state=StrategyState.ACTIVE, matched_at=context_at,
             valid_until=context_until),
        atom("range_breakout", timeframe="M5", role="TRIGGER",
             state=StrategyState.MATCHED, matched_at=trigger_at),
    ]


def test_context_trigger_matches_when_the_context_was_established_and_still_valid():
    envelope = evaluate(
        context_trigger_chain(),
        context_and_trigger(
            context_at=at(hours=-4),
            trigger_at=at(minutes=-5),
            context_until=at(hours=4),
        ),
    )
    assert envelope.state is StrategyState.MATCHED
    assert "still valid when the trigger matched" in envelope.explanation


def test_context_trigger_does_not_match_when_the_context_came_after_the_trigger():
    envelope = evaluate(
        context_trigger_chain(),
        context_and_trigger(context_at=at(minutes=-1), trigger_at=at(minutes=-30)),
    )
    assert envelope.state is StrategyState.FORMING
    assert "CONTEXT_NOT_ESTABLISHED_FIRST" in envelope.explanation


def test_context_trigger_does_not_match_when_the_context_validity_had_lapsed():
    envelope = evaluate(
        context_trigger_chain(),
        context_and_trigger(
            context_at=at(hours=-8),
            trigger_at=at(minutes=-5),
            context_until=at(hours=-1),
        ),
    )
    assert envelope.state is StrategyState.FORMING
    assert "COMPONENT_VALIDITY_LAPSED" in envelope.explanation
    assert envelope.evidence["first_unsatisfied_component"] == "golden_cross@1.0.0"


def test_a_context_that_lapsed_after_the_trigger_still_fails_the_relationship():
    """The context is live now, but had already lapsed when the trigger fired."""
    envelope = evaluate(
        context_trigger_chain(),
        context_and_trigger(
            context_at=at(hours=-8),
            trigger_at=at(minutes=-5),
            context_until=at(minutes=-10),
        ),
        now=at(minutes=-11),
    )
    assert envelope.state is StrategyState.FORMING
    assert "CONTEXT_VALIDITY_LAPSED" in envelope.explanation


def test_context_trigger_requires_the_trigger_to_have_fired():
    envelope = evaluate(
        context_trigger_chain(),
        [
            atom("golden_cross", timeframe="H4", role="CONTEXT",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-4)),
            atom("range_breakout", timeframe="M5", role="TRIGGER",
                 state=StrategyState.DORMANT),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert envelope.evidence["first_unsatisfied_component"] == "range_breakout@1.0.0"


def test_an_intermediate_component_must_hold_too():
    package = chain_package(
        strategy_id="proof_three_stage_context_trigger",
        primitive="CONTEXT_TRIGGER",
        components=[
            component("golden_cross", role="CONTEXT"),
            component("rejection_wick", role="CONFIRMATION"),
            component("range_breakout", role="TRIGGER", required_states=("MATCHED",)),
        ],
        role_timeframes={"CONTEXT": "H4", "CONFIRMATION": "M15", "TRIGGER": "M5"},
    )
    envelope = evaluate(
        package,
        [
            atom("golden_cross", timeframe="H4", role="CONTEXT",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-4)),
            atom("rejection_wick", timeframe="M15", role="CONFIRMATION",
                 state=StrategyState.DORMANT),
            atom("range_breakout", timeframe="M5", role="TRIGGER",
                 state=StrategyState.MATCHED, matched_at=at(minutes=-5)),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "rejection_wick@1.0.0 (CONFIRMATION, M15)" in envelope.explanation


def test_the_engine_orders_context_first_whatever_order_the_package_declares():
    package = chain_package(
        strategy_id="proof_declared_backwards",
        primitive="CONTEXT_TRIGGER",
        components=[
            component("range_breakout", role="TRIGGER", required_states=("MATCHED",)),
            component("golden_cross", role="CONTEXT"),
        ],
        role_timeframes={"CONTEXT": "H4", "TRIGGER": "M5"},
    )
    engine = ChainEngine(package)
    assert [str(item.role) for item in engine.ordered_components] == [
        "CONTEXT",
        "TRIGGER",
    ]


def test_a_context_on_a_finer_timeframe_than_its_trigger_is_refused():
    from helios.errors import StrategySpecError

    package = context_trigger_chain(role_timeframes={"CONTEXT": "M5", "TRIGGER": "H4"})
    with pytest.raises(StrategySpecError) as raised:
        ChainEngine(package)
    assert "finer timeframe" in str(raised.value)


def test_the_reason_vocabulary_is_complete_enough_to_be_useful():
    """Every non-satisfying reason names a rule, not an accident."""
    assert ComponentReason.SATISFIED.value == "SATISFIED"
    assert len([member for member in ComponentReason]) >= 12
