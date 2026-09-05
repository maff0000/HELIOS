"""Provenance, explanation, determinism and replay.

A chain must be able to state exactly which component versions produced its
state, explain itself whether or not it matched, and produce byte-identical
output when the same ordered inputs are replayed.
"""

from __future__ import annotations

from helios.composition import ChainEngine
from helios.contracts.output import EnvelopeKind, StrategyStateEnvelope
from helios.contracts.state import Direction, StrategyState
from tests.test_chain_support import (
    INSTRUMENT,
    T0,
    at,
    atom,
    chain_package,
    component,
    fact,
)


def simple_chain(**kwargs):
    return chain_package(
        strategy_id="proof_provenance",
        primitive="ALL",
        components=[
            component("golden_cross", role="CONTEXT"),
            component("range_breakout", role="TRIGGER", version="2.1.0"),
        ],
        role_timeframes={"CONTEXT": "H4", "TRIGGER": "M5"},
        **kwargs,
    )


def holding(evaluated_at=None, matched_at=None):
    return [
        atom("golden_cross", timeframe="H4", role="CONTEXT",
             state=StrategyState.ACTIVE, evaluated_at=evaluated_at,
             matched_at=matched_at or at(hours=-4)),
        atom("range_breakout", version="2.1.0", timeframe="M5", role="TRIGGER",
             state=StrategyState.MATCHED, evaluated_at=evaluated_at,
             matched_at=matched_at or at(minutes=-5)),
    ]


def run(engine, components, now=None, previous=None):
    return engine.evaluate(
        instrument=INSTRUMENT,
        evaluated_at_utc=now or T0,
        components=components,
        previous=previous,
    )


# ----------------------------------------------------------------- identity


def test_a_chain_publishes_its_identity_in_both_contract_forms():
    envelope = run(ChainEngine(simple_chain()), holding())
    assert envelope.kind is EnvelopeKind.CHAIN
    assert str(envelope.chain_id) == "proof_provenance"
    assert str(envelope.chain_version) == "1.0.0"
    assert str(envelope.strategy_id) == str(envelope.chain_id)
    assert envelope.strategy_version == envelope.chain_version


def test_a_chain_states_exactly_which_component_versions_produced_its_state():
    envelope = run(ChainEngine(simple_chain()), holding())
    published = {
        str(item.strategy_id): str(item.strategy_version) for item in envelope.components
    }
    assert published == {"golden_cross": "1.0.0", "range_breakout": "2.1.0"}


def test_provenance_carries_role_timeframe_state_and_direction_per_component():
    envelope = run(ChainEngine(simple_chain()), holding())
    context = envelope.components[0]
    assert str(context.strategy_id) == "golden_cross"
    assert str(context.semantic_role) == "CONTEXT"
    assert context.timeframe.code == "H4"
    assert context.state is StrategyState.ACTIVE
    assert context.direction is Direction.LONG
    assert context.matched is True
    assert context.contribution.startswith("SATISFIED")


def test_provenance_is_published_for_every_declared_component_even_an_absent_one():
    envelope = run(ChainEngine(simple_chain()), [holding()[0]])
    assert [str(item.strategy_id) for item in envelope.components] == [
        "golden_cross",
        "range_breakout",
    ]
    absent = envelope.components[1]
    assert str(absent.strategy_version) == "2.1.0"
    assert absent.timeframe.code == "M5"
    assert absent.matched is False


def test_a_chain_republishes_the_freshness_of_every_fact_behind_it():
    components = [
        atom("golden_cross", timeframe="H4", role="CONTEXT",
             state=StrategyState.ACTIVE, matched_at=at(hours=-4),
             inputs=[fact(timeframe="H4", role="CONTEXT")]),
        atom("range_breakout", version="2.1.0", timeframe="M5", role="TRIGGER",
             state=StrategyState.MATCHED, matched_at=at(minutes=-5),
             inputs=[fact(timeframe="M5", role="TRIGGER", age_seconds=30,
                          max_age_seconds=600)]),
    ]
    envelope = run(ChainEngine(simple_chain()), components)
    published = {item.timeframe.code: item.age_seconds for item in envelope.inputs}
    assert published == {"H4": 60, "M5": 30}
    assert all(item.is_fresh for item in envelope.inputs)


def test_identical_facts_behind_two_components_are_published_once():
    shared = fact(timeframe="H4", role="CONTEXT")
    components = [
        atom("golden_cross", timeframe="H4", role="CONTEXT",
             state=StrategyState.ACTIVE, matched_at=at(hours=-4), inputs=[shared]),
        atom("range_breakout", version="2.1.0", timeframe="M5", role="TRIGGER",
             state=StrategyState.MATCHED, matched_at=at(minutes=-5), inputs=[shared]),
    ]
    envelope = run(ChainEngine(simple_chain()), components)
    assert len(envelope.inputs) == 1


# -------------------------------------------------------------- explanation


def test_a_match_is_explained():
    envelope = run(ChainEngine(simple_chain()), holding())
    assert envelope.explanation
    assert "matched LONG" in envelope.explanation
    assert "golden_cross@1.0.0 (CONTEXT, H4)" in envelope.explanation
    assert "range_breakout@2.1.0 (TRIGGER, M5)" in envelope.explanation
    assert "2 of 2 components satisfied" in envelope.explanation
    assert "published state MATCHED" in envelope.explanation


def test_a_non_match_is_explained_as_fully_as_a_match():
    envelope = run(ChainEngine(simple_chain()), [holding()[0]])
    assert "did not match" in envelope.explanation
    assert "range_breakout@2.1.0 (TRIGGER, M5) is not satisfied" in envelope.explanation
    assert "NOT_SUPPLIED" in envelope.explanation
    # The component that DID hold is still named, so a reader sees the whole picture.
    assert "Still holding" in envelope.explanation
    assert "golden_cross@1.0.0" in envelope.explanation
    assert "1 of 2 components satisfied" in envelope.explanation


def test_the_explanation_names_the_state_and_the_rule_that_produced_it():
    engine = ChainEngine(simple_chain(min_matched_frames=2))
    envelope = run(engine, holding())
    assert envelope.state is StrategyState.FORMING
    assert "held on 1 of the 2 consecutive evaluations" in envelope.explanation


# ------------------------------------------------------ determinism / replay


def test_two_identical_evaluations_serialise_identically():
    engine = ChainEngine(simple_chain())
    first = run(engine, holding())
    second = run(ChainEngine(simple_chain()), holding())
    assert first.to_canonical_json() == second.to_canonical_json()


def test_a_published_chain_envelope_round_trips_through_canonical_json():
    envelope = run(ChainEngine(simple_chain()), holding())
    text = envelope.to_canonical_json()
    restored = StrategyStateEnvelope.from_canonical_json(text)
    assert restored.to_canonical_json() == text
    assert restored.state is envelope.state
    assert restored.components == envelope.components


def test_replaying_the_same_ordered_inputs_reproduces_every_envelope():
    def replay():
        engine = ChainEngine(simple_chain(min_matched_frames=2))
        previous = None
        produced = []
        for step in range(6):
            moment = at(minutes=5 * step)
            if step < 4:
                components = holding(evaluated_at=moment)
            else:
                components = [
                    atom("golden_cross", timeframe="H4", role="CONTEXT",
                         state=StrategyState.DORMANT, evaluated_at=moment),
                    atom("range_breakout", version="2.1.0", timeframe="M5",
                         role="TRIGGER", state=StrategyState.DORMANT,
                         evaluated_at=moment),
                ]
            previous = ChainEngine(simple_chain(min_matched_frames=2)).evaluate(
                instrument=INSTRUMENT,
                evaluated_at_utc=moment,
                components=components,
                previous=previous,
            )
            produced.append(previous.to_canonical_json())
        return produced

    first = replay()
    second = replay()
    assert first == second
    states = [
        StrategyStateEnvelope.from_canonical_json(text).state for text in first
    ]
    assert states == [
        StrategyState.FORMING,
        StrategyState.MATCHED,
        StrategyState.ACTIVE,
        StrategyState.ACTIVE,
        StrategyState.INVALID,
        StrategyState.DORMANT,
    ]


def test_the_engine_holds_no_state_between_evaluations():
    """Replay from a stored envelope, on a brand new engine, is identical."""
    engine = ChainEngine(simple_chain(min_matched_frames=2))
    first = run(engine, holding())
    continued = run(engine, holding(evaluated_at=at(minutes=5)), at(minutes=5),
                    previous=first)

    stored = StrategyStateEnvelope.from_canonical_json(first.to_canonical_json())
    from_storage = ChainEngine(simple_chain(min_matched_frames=2)).evaluate(
        instrument=INSTRUMENT,
        evaluated_at_utc=at(minutes=5),
        components=holding(evaluated_at=at(minutes=5)),
        previous=stored,
    )
    assert from_storage.to_canonical_json() == continued.to_canonical_json()


def test_the_order_component_states_are_supplied_in_does_not_change_the_result():
    engine = ChainEngine(simple_chain())
    forwards = run(engine, holding())
    backwards = run(engine, list(reversed(holding())))
    assert forwards.to_canonical_json() == backwards.to_canonical_json()
