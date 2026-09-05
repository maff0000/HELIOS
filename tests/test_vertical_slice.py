"""The vertical slice, end to end, in process.

The PID's initial slice, in its own words:

    HERMES-compatible market-fact input -> multiple independent atomic strategy
    evaluations -> normalised atomic strategy state -> composition engine -> at
    least one non-trivial chain -> normalised chain state -> FALCON-compatible
    downstream contract

That is what runs here. Not a reconstruction of it: the deployment is assembled
by ``build_runtime`` from configuration, the same call ``python3 -m
helios.runtime`` makes, over the checked-in time-aligned facts and the
checked-in HSA handoff. Every payload asserted below is a payload the service
publishes.

Every expected instant and state in this file was worked out by hand from
``docs/FIXTURES.md`` §5 before it was asserted, the same way the atom tests
were, and the fixture-level numbers behind them are checked independently in
``tests/test_scenario_fixtures.py``.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from decimal import Decimal

import pytest

from helios.contracts.output import ENVELOPE_SCHEMA_VERSION, EnvelopeKind
from helios.contracts.state import Direction, LIVE_STATES, StrategyState
from helios.publish import InMemorySink, StatePublisher, accept_payload
from tests._scenario import (
    MATCH_INSTANT,
    SCENARIO_UNITS,
    build_scenario_runtime,
    replay,
)

#: What each unit publishes over the whole replay, run-length encoded.
#: Read as "this state, for this many consecutive evaluations".
EXPECTED_SEQUENCES: dict[str, list[tuple[str, int]]] = {
    "golden_cross": [("DORMANT", 12), ("MATCHED", 1), ("ACTIVE", 48)],
    "swing_proximity": [("DORMANT", 24), ("MATCHED", 1), ("ACTIVE", 36)],
    "rejection_wick": [
        ("DORMANT", 33),
        ("MATCHED", 1),
        ("ACTIVE", 8),
        ("INVALID", 1),
        ("DORMANT", 18),
    ],
    "range_breakout": [
        ("DORMANT", 5),
        ("FORMING", 5),
        ("DORMANT", 3),
        ("FORMING", 12),
        ("DORMANT", 6),
        ("FORMING", 1),
        ("DORMANT", 2),
        ("FORMING", 1),
        ("DORMANT", 1),
        ("MATCHED", 1),
        ("ACTIVE", 12),
        ("EXPIRED", 1),
        ("DORMANT", 3),
        ("FORMING", 3),
        ("DORMANT", 5),
    ],
    "gold_continuous_sequence": [
        ("DORMANT", 12),
        ("FORMING", 24),
        ("MATCHED", 1),
        ("ACTIVE", 5),
        ("INVALID", 1),
        ("DORMANT", 1),
        ("FORMING", 17),
    ],
    "gold_continuous_context_trigger": [
        ("DORMANT", 12),
        ("FORMING", 24),
        ("MATCHED", 1),
        ("ACTIVE", 12),
        ("EXPIRED", 1),
        ("DORMANT", 1),
        ("FORMING", 10),
    ],
}

#: When each unit's occurrence first matched. These are what the SEQUENCE
#: chain's ordering is decided from, and they ascend through the declared
#: stages: CONTEXT, then LOCATION, then CONFIRMATION, then TRIGGER.
EXPECTED_FIRST_MATCH = {
    "golden_cross": "2026-03-02T12:01:00+00:00",
    "swing_proximity": "2026-03-02T13:01:00+00:00",
    "rejection_wick": "2026-03-02T13:46:00+00:00",
    "range_breakout": "2026-03-02T14:01:00+00:00",
}


@pytest.fixture(scope="module")
def payloads(tmp_path_factory) -> tuple[str, ...]:
    return replay(tmp_path_factory.mktemp("slice") / "status.json")


@pytest.fixture(scope="module")
def envelopes(payloads):
    """Every payload, read back through the boundary a FALCON consumer uses."""
    return tuple(accept_payload(line) for line in payloads)


@pytest.fixture(scope="module")
def by_unit(envelopes):
    grouped: dict[str, list] = {}
    for envelope in envelopes:
        grouped.setdefault(str(envelope.strategy_id), []).append(envelope)
    return grouped


def at_instant(envelopes, instant: str):
    from helios.clock import from_iso8601_utc

    moment = from_iso8601_utc(instant)
    return {
        str(envelope.strategy_id): envelope
        for envelope in envelopes
        if envelope.last_evaluated_at_utc == moment
    }


# ------------------------------------------------------- the slice, end to end


def test_the_whole_slice_runs_and_publishes(payloads, by_unit):
    """Input, atoms, chain, published contract — nothing simulated."""
    assert set(by_unit) == set(SCENARIO_UNITS)
    assert len(payloads) == 366
    assert {len(items) for items in by_unit.values()} == {61}


def test_publication_order_is_canonical_and_atoms_precede_chains(envelopes):
    """Ordering is fixed by identity, never by how a directory enumerated."""
    cycle = [str(envelope.strategy_id) for envelope in envelopes[: len(SCENARIO_UNITS)]]
    assert cycle == list(SCENARIO_UNITS)
    kinds = [envelope.kind for envelope in envelopes[: len(SCENARIO_UNITS)]]
    assert kinds == [EnvelopeKind.ATOMIC] * 4 + [EnvelopeKind.CHAIN] * 2


def test_every_payload_is_the_normalised_falcon_envelope(envelopes, payloads):
    """One envelope for atoms and chains alike, byte-stable, self-describing."""
    for envelope, payload in zip(envelopes, payloads):
        assert envelope.schema_version == ENVELOPE_SCHEMA_VERSION
        assert envelope.to_canonical_json() == payload
        assert str(envelope.instrument) == "XAU_USD"
        if envelope.kind is EnvelopeKind.CHAIN:
            assert str(envelope.chain_id) == str(envelope.strategy_id)
            assert envelope.chain_version == envelope.strategy_version
        else:
            assert envelope.chain_id is None


@pytest.mark.parametrize("unit", sorted(EXPECTED_SEQUENCES))
def test_each_unit_publishes_the_lifecycle_it_should(unit, by_unit):
    observed = [
        (state, len(list(group)))
        for state, group in itertools.groupby(
            envelope.state.value for envelope in by_unit[unit]
        )
    ]
    assert observed == EXPECTED_SEQUENCES[unit]


def test_multiple_independent_atomic_strategies_are_evaluated_concurrently(by_unit):
    """Four atoms, four timeframes, one instant, no strategy aware of another."""
    atoms = ("golden_cross", "swing_proximity", "rejection_wick", "range_breakout")
    timeframes = {
        unit: by_unit[unit][0].timeframe.code for unit in atoms
    }
    assert timeframes == {
        "golden_cross": "H4",
        "swing_proximity": "H1",
        "rejection_wick": "M15",
        "range_breakout": "M5",
    }
    roles = {unit: str(by_unit[unit][0].semantic_role) for unit in atoms}
    assert roles == {
        "golden_cross": "CONTEXT",
        "swing_proximity": "LOCATION",
        "rejection_wick": "CONFIRMATION",
        "range_breakout": "TRIGGER",
    }


def test_no_strategy_evaluation_had_to_be_contained(envelopes):
    """A contained failure publishes INVALID with a failure type in evidence."""
    from helios.strategies import FAILURE_KEY

    assert not [
        envelope for envelope in envelopes if FAILURE_KEY in envelope.evidence
    ]


# -------------------------------------------------------- the match instant


def test_every_timeframe_is_fresh_at_the_match_instant(envelopes):
    """The property the single-atom fixtures could not provide."""
    published = at_instant(envelopes, MATCH_INSTANT)
    chain = published["gold_continuous_sequence"]
    freshness = {item.timeframe.code: item for item in chain.inputs}
    assert set(freshness) == {"H4", "H1", "M15", "M5"}
    for code, item in freshness.items():
        assert item.is_fresh, code
        assert item.age_seconds <= item.max_age_seconds, code


def test_the_four_stage_sequence_matched_for_real(envelopes):
    published = at_instant(envelopes, MATCH_INSTANT)
    chain = published["gold_continuous_sequence"]
    assert chain.state is StrategyState.MATCHED
    assert chain.direction is Direction.LONG
    assert chain.strength == Decimal("1.0000")
    assert chain.evidence["components_satisfied"] == 4
    assert chain.evidence["components_declared"] == 4
    assert chain.evidence["primitive"] == "SEQUENCE"


def test_the_chain_preserves_every_component_s_provenance(envelopes):
    published = at_instant(envelopes, MATCH_INSTANT)
    chain = published["gold_continuous_sequence"]
    assert [
        (
            str(component.strategy_id),
            str(component.strategy_version),
            str(component.semantic_role),
            component.timeframe.code,
            component.sequence_index,
            component.matched,
        )
        for component in chain.components
    ] == [
        ("golden_cross", "1.0.0", "CONTEXT", "H4", 0, True),
        ("swing_proximity", "1.0.0", "LOCATION", "H1", 1, True),
        ("rejection_wick", "1.0.0", "CONFIRMATION", "M15", 2, True),
        ("range_breakout", "1.0.0", "TRIGGER", "M5", 3, True),
    ]
    for component in chain.components:
        assert component.contribution.startswith("SATISFIED:")


def test_the_components_matched_in_the_declared_order_and_within_the_window(envelopes):
    """What the SEQUENCE ordering is actually decided from.

    ``first_matched_at_utc`` — when THIS occurrence began — not
    ``last_matched_at_utc``, which advances on every live evaluation and would
    make a component that matched first appear to have matched last. Read at
    the match instant, because a resolved occurrence clears it again.
    """
    published = at_instant(envelopes, MATCH_INSTANT)
    first_matched = {
        unit: published[unit].first_matched_at_utc for unit in EXPECTED_FIRST_MATCH
    }
    assert {
        unit: moment.isoformat() for unit, moment in first_matched.items()
    } == EXPECTED_FIRST_MATCH
    ordered = [
        first_matched[unit]
        for unit in ("golden_cross", "swing_proximity", "rejection_wick", "range_breakout")
    ]
    assert ordered == sorted(ordered)
    assert ordered[-1] - ordered[0] <= timedelta(seconds=28800)


def test_the_context_trigger_chain_matched_at_the_same_instant(envelopes):
    published = at_instant(envelopes, MATCH_INSTANT)
    chain = published["gold_continuous_context_trigger"]
    assert chain.state is StrategyState.MATCHED
    assert chain.direction is Direction.LONG
    assert chain.evidence["primitive"] == "CONTEXT_TRIGGER"
    assert "the context was established" in chain.explanation
    assert [str(component.semantic_role) for component in chain.components] == [
        "CONTEXT",
        "TRIGGER",
    ]


def test_the_whole_slice_agrees_on_one_direction(envelopes):
    published = at_instant(envelopes, MATCH_INSTANT)
    assert {
        unit: envelope.direction for unit, envelope in published.items()
    } == {unit: Direction.LONG for unit in SCENARIO_UNITS}


# ------------------------------------------------------- how it ends, and why


def test_a_chain_resolves_explicitly_and_says_which_component_ended_it(by_unit):
    """The SEQUENCE is voided when its confirmation is invalidated."""
    resolved = [
        envelope
        for envelope in by_unit["gold_continuous_sequence"]
        if envelope.state is StrategyState.INVALID
    ]
    assert len(resolved) == 1
    envelope = resolved[0]
    assert envelope.last_evaluated_at_utc.isoformat() == "2026-03-02T14:31:00+00:00"
    assert "rejection_wick@1.0.0" in envelope.explanation
    assert envelope.evidence["first_unsatisfied_component"] == "rejection_wick@1.0.0"
    assert envelope.validity.reason


def test_the_other_chain_ages_out_rather_than_breaking(by_unit):
    """Distinct on purpose: something broke, versus time ran out."""
    aged = [
        envelope
        for envelope in by_unit["gold_continuous_context_trigger"]
        if envelope.state is StrategyState.EXPIRED
    ]
    assert len(aged) == 1
    envelope = aged[0]
    assert envelope.last_evaluated_at_utc.isoformat() == "2026-03-02T15:06:00+00:00"
    assert envelope.validity.valid_from_utc.isoformat() == "2026-03-02T14:01:00+00:00"
    assert envelope.validity.valid_until_utc.isoformat() == "2026-03-02T15:01:00+00:00"
    # FRAMES expiry on a chain counts frames of the FINEST timeframe it binds.
    assert envelope.evidence["expiry_mode"] == "FRAMES"
    assert envelope.evidence["validity_seconds"] == 3600


def test_a_resolved_chain_rearms_only_through_dormant(by_unit):
    for unit in ("gold_continuous_sequence", "gold_continuous_context_trigger"):
        states = [envelope.state for envelope in by_unit[unit]]
        for earlier, later in zip(states, states[1:]):
            if earlier in (StrategyState.INVALID, StrategyState.EXPIRED):
                assert later in (earlier, StrategyState.DORMANT), (unit, earlier, later)


def test_every_non_match_is_explained(envelopes):
    """The PID requires an explicit explanation either way."""
    for envelope in envelopes:
        assert envelope.explanation.strip()
        if envelope.kind is EnvelopeKind.CHAIN and envelope.state not in LIVE_STATES:
            assert "did not match" in envelope.explanation or envelope.state in (
                StrategyState.INVALID,
                StrategyState.EXPIRED,
            )


# ------------------------------------------------------------- reproducibility


def test_replaying_the_same_ordered_input_publishes_identical_bytes(
    payloads, tmp_path
):
    """The determinism claim, made checkable: nothing is added at publication."""
    assert replay(tmp_path / "status.json") == payloads


def test_two_runtimes_built_independently_publish_identical_bytes(payloads, tmp_path):
    first = InMemorySink()
    second = InMemorySink()
    for sink, name in ((first, "one"), (second, "two")):
        runtime = build_scenario_runtime(
            tmp_path / f"status-{name}.json", publisher=StatePublisher(sink)
        )
        for instant in runtime.instants:
            runtime.evaluate_once(instant)
    assert first.records == second.records == payloads
