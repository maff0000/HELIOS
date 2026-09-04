"""Direction resolution and compatibility across a chain.

Direction is the semantics most easily got wrong by accident, because "the
chain's direction" and "which components count" depend on each other. HELIOS
breaks the loop in one stated order: resolve from the components that hold on
their own terms, then check every component against the result. These tests
pin that order down, including the cases where it matters most — ``OPPOSITE``
relationships, ``NEUTRAL`` versus ``NONE``, and a live chain whose anchor
flips.
"""

from __future__ import annotations

import pytest

from helios.composition import ChainEngine, find_anchor, is_compatible
from helios.contracts.state import Direction, StrategyState
from helios.errors import StrategySpecError
from helios.spec.model import DirectionRelationship
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


def same_chain(**kwargs):
    return chain_package(
        strategy_id="proof_same_direction",
        primitive="ALL",
        components=[component("golden_cross"), component("range_breakout")],
        **kwargs,
    )


def opposite_chain(**kwargs):
    return chain_package(
        strategy_id="proof_opposite_direction",
        primitive="ALL",
        components=[
            component("golden_cross", relationship="SAME"),
            component("fade_extreme", relationship="OPPOSITE"),
        ],
        relationship="OPPOSITE",
        **kwargs,
    )


# ------------------------------------------------------------------- SAME


def test_a_chain_adopts_the_direction_of_its_anchor_component():
    envelope = evaluate(
        same_chain(),
        [
            atom("golden_cross", direction=Direction.SHORT),
            atom("range_breakout", direction=Direction.SHORT),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.SHORT


def test_a_component_disagreeing_with_the_chain_direction_does_not_count():
    envelope = evaluate(
        same_chain(),
        [
            atom("golden_cross", direction=Direction.LONG),
            atom("range_breakout", direction=Direction.SHORT),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "DIRECTION_INCOMPATIBLE" in envelope.explanation
    assert "is not SAME the chain direction LONG" in envelope.explanation


def test_a_neutral_component_does_not_agree_with_a_directional_chain():
    """NEUTRAL is 'no bias', not 'any bias'."""
    envelope = evaluate(
        same_chain(),
        [
            atom("golden_cross", direction=Direction.LONG),
            atom("range_breakout", direction=Direction.NEUTRAL),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "DIRECTION_INCOMPATIBLE" in envelope.explanation


# --------------------------------------------------------------- OPPOSITE


def test_an_opposite_component_must_oppose_the_chain_direction():
    envelope = evaluate(
        opposite_chain(),
        [
            atom("golden_cross", direction=Direction.LONG),
            atom("fade_extreme", direction=Direction.SHORT),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG


def test_an_opposite_component_agreeing_with_the_chain_does_not_count():
    envelope = evaluate(
        opposite_chain(),
        [
            atom("golden_cross", direction=Direction.LONG),
            atom("fade_extreme", direction=Direction.LONG),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "is not OPPOSITE the chain direction LONG" in envelope.explanation


def test_an_opposite_component_never_defines_the_chain_direction():
    """Only a SAME component can be the anchor; an OPPOSITE one would invert it."""
    package = chain_package(
        strategy_id="proof_anchor_order",
        primitive="ALL",
        components=[
            component("fade_extreme", relationship="OPPOSITE"),
            component("golden_cross", relationship="SAME"),
        ],
        relationship="OPPOSITE",
    )
    envelope = evaluate(
        package,
        [
            atom("fade_extreme", direction=Direction.SHORT),
            atom("golden_cross", direction=Direction.LONG),
        ],
    )
    assert envelope.direction is Direction.LONG
    assert envelope.state is StrategyState.MATCHED


def test_neither_neutral_nor_non_directional_can_satisfy_opposite():
    for direction in (Direction.NEUTRAL, Direction.NONE):
        envelope = evaluate(
            opposite_chain(),
            [
                atom("golden_cross", direction=Direction.LONG),
                atom("fade_extreme", direction=direction),
            ],
        )
        assert envelope.state is StrategyState.FORMING


# -------------------------------------------------------------------- ANY


def test_an_any_relationship_lets_a_component_participate_on_state_alone():
    package = chain_package(
        strategy_id="proof_any_relationship",
        primitive="ALL",
        components=[
            component("golden_cross", relationship="SAME"),
            component("volatility_regime", relationship="ANY"),
        ],
    )
    envelope = evaluate(
        package,
        [
            atom("golden_cross", direction=Direction.LONG),
            atom("volatility_regime", direction=Direction.NONE),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG


# ------------------------------------------------------- non-directional


def test_a_non_directional_chain_publishes_none_not_neutral():
    package = chain_package(
        strategy_id="proof_non_directional",
        primitive="ALL",
        components=[
            component("volatility_regime", relationship="ANY"),
            component("session_filter", relationship="ANY"),
        ],
        mode="NON_DIRECTIONAL",
        relationship="ANY",
    )
    envelope = evaluate(
        package,
        [
            atom("volatility_regime", direction=Direction.NONE),
            atom("session_filter", direction=Direction.NONE),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.NONE


def test_a_directional_chain_with_no_directional_anchor_publishes_neutral():
    envelope = evaluate(
        same_chain(),
        [
            atom("golden_cross", direction=Direction.NEUTRAL),
            atom("range_breakout", direction=Direction.NEUTRAL),
        ],
    )
    assert envelope.direction is Direction.NEUTRAL
    assert envelope.state is StrategyState.MATCHED


# ------------------------------------------------- definition-time refusals


def test_a_chain_declaring_same_overall_may_not_declare_an_opposite_component():
    package = chain_package(
        strategy_id="proof_contradictory_doctrine",
        primitive="ALL",
        components=[
            component("golden_cross", relationship="SAME"),
            component("fade_extreme", relationship="OPPOSITE"),
        ],
        relationship="SAME",
    )
    with pytest.raises(StrategySpecError) as raised:
        ChainEngine(package)
    assert "contradict" in str(raised.value)


def test_a_chain_declaring_opposite_overall_must_declare_an_opposite_component():
    package = chain_package(
        strategy_id="proof_absent_opposite",
        primitive="ALL",
        components=[component("golden_cross"), component("range_breakout")],
        relationship="OPPOSITE",
    )
    with pytest.raises(StrategySpecError) as raised:
        ChainEngine(package)
    assert "no component declares OPPOSITE" in str(raised.value)


def test_a_directional_chain_without_an_anchor_is_refused():
    package = chain_package(
        strategy_id="proof_anchorless",
        primitive="ALL",
        components=[
            component("golden_cross", relationship="ANY"),
            component("range_breakout", relationship="ANY"),
        ],
        relationship="ANY",
    )
    with pytest.raises(StrategySpecError) as raised:
        ChainEngine(package)
    assert "direction_relationship SAME" in str(raised.value)


# ---------------------------------------------------------- pinned direction


def test_a_live_chain_is_voided_rather_than_flipped_when_its_anchor_reverses():
    package = same_chain()
    first = evaluate(
        package,
        [
            atom("golden_cross", direction=Direction.LONG),
            atom("range_breakout", direction=Direction.LONG),
        ],
    )
    assert first.state is StrategyState.MATCHED and first.direction is Direction.LONG

    later = at(minutes=5)
    second = evaluate(
        package,
        [
            atom("golden_cross", direction=Direction.SHORT, evaluated_at=later,
                 matched_at=later),
            atom("range_breakout", direction=Direction.SHORT, evaluated_at=later,
                 matched_at=later),
        ],
        now=later,
        previous=first,
    )
    assert second.state is StrategyState.INVALID
    assert second.direction is Direction.LONG
    assert "DIRECTION_INCOMPATIBLE" in second.explanation


# ------------------------------------------------------------------ units


def test_direction_compatibility_rules_in_isolation():
    same = DirectionRelationship.SAME
    opposite = DirectionRelationship.OPPOSITE
    any_relationship = DirectionRelationship.ANY

    assert is_compatible(same, Direction.LONG, Direction.LONG)
    assert not is_compatible(same, Direction.NEUTRAL, Direction.LONG)
    assert is_compatible(same, Direction.NEUTRAL, Direction.NEUTRAL)
    assert is_compatible(same, Direction.NONE, Direction.NONE)
    assert is_compatible(opposite, Direction.SHORT, Direction.LONG)
    assert not is_compatible(opposite, Direction.LONG, Direction.LONG)
    assert not is_compatible(opposite, Direction.NEUTRAL, Direction.LONG)
    for direction in Direction:
        assert is_compatible(any_relationship, direction, Direction.LONG)


def test_the_anchor_is_the_first_holding_same_component_in_canonical_order():
    package = chain_package(
        strategy_id="proof_anchor_selection",
        primitive="ALL",
        components=[
            component("golden_cross", relationship="SAME"),
            component("range_breakout", relationship="SAME"),
        ],
    )
    engine = ChainEngine(package)
    assessment = engine.assess(
        instrument=INSTRUMENT,
        evaluated_at_utc=T0,
        components=[
            atom("golden_cross", state=StrategyState.DORMANT),
            atom("range_breakout", direction=Direction.SHORT),
        ],
    )
    anchor = find_anchor(assessment.outcomes)
    assert anchor is not None
    assert str(anchor.declared.strategy_id) == "range_breakout"
    assert assessment.direction is Direction.SHORT
