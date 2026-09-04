"""Failure, isolation and the boundaries the engine refuses to cross.

Two different kinds of thing go wrong, and HELIOS must treat them differently:

* **Market conditions** — a component is absent, stale, invalidated or aged
  out. These are ordinary. They resolve by explicit rule into a well-defined
  chain state with an explanation. They never raise and never silently pass.
* **Wiring and definition faults** — a component state for the wrong
  instrument, two states for one component identity, a chain handed another
  chain's envelope, an atomic package handed to the chain engine. These are
  bugs. They fail loudly rather than producing a plausible-looking result.
"""

from __future__ import annotations

import pytest

from helios.composition import ChainEngine
from helios.contracts.output import EnvelopeKind
from helios.contracts.state import Direction, StrategyState
from helios.errors import ContractViolationError, StrategySpecError
from helios.spec.loader import load_strategy_package
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
        strategy_id="proof_failures",
        primitive="ALL",
        components=[component("golden_cross"), component("range_breakout")],
        **kwargs,
    )


def run(engine, components, now=None, previous=None, instrument=INSTRUMENT):
    return engine.evaluate(
        instrument=instrument,
        evaluated_at_utc=now or T0,
        components=components,
        previous=previous,
    )


# ------------------------------------------------------- market conditions


def test_a_missing_component_resolves_by_rule_rather_than_crashing():
    envelope = run(ChainEngine(simple_chain()), [atom("golden_cross")])
    assert envelope.state is StrategyState.FORMING
    assert "NOT_SUPPLIED" in envelope.explanation
    absent = [
        item for item in envelope.components if str(item.strategy_id) == "range_breakout"
    ][0]
    assert absent.state is StrategyState.INVALID
    assert absent.matched is False
    assert absent.contribution.startswith("NOT_SUPPLIED")
    assert str(absent.strategy_version) == "1.0.0"


def test_no_component_at_all_is_dormant_and_still_fully_explained():
    envelope = run(ChainEngine(simple_chain()), [])
    assert envelope.state is StrategyState.DORMANT
    assert len(envelope.components) == 2
    assert envelope.explanation
    assert "golden_cross@1.0.0" in envelope.explanation
    assert "range_breakout@1.0.0" in envelope.explanation


def test_a_component_supplied_for_another_version_is_not_silently_accepted():
    envelope = run(
        ChainEngine(simple_chain()),
        [atom("golden_cross"), atom("range_breakout", version="2.0.0")],
    )
    assert envelope.state is StrategyState.FORMING
    assert "VERSION_MISMATCH" in envelope.explanation
    assert "supplied only for version(s) 2.0.0" in envelope.explanation
    assert envelope.evidence["first_unsatisfied_reason"] == "VERSION_MISMATCH"


def test_a_component_resting_on_a_stale_fact_does_not_count():
    envelope = run(
        ChainEngine(simple_chain()),
        [
            atom("golden_cross"),
            atom(
                "range_breakout",
                inputs=[fact(is_fresh=False, age_seconds=90000, max_age_seconds=18000)],
            ),
        ],
    )
    assert envelope.state is StrategyState.FORMING
    assert "STALE_COMPONENT_INPUT" in envelope.explanation
    assert "the oldest at 90000s against a limit of 18000s" in envelope.explanation


def test_an_invalid_component_never_silently_passes():
    envelope = run(
        ChainEngine(simple_chain()),
        [atom("golden_cross"), atom("range_breakout", state=StrategyState.INVALID)],
    )
    assert envelope.state is StrategyState.FORMING
    assert "COMPONENT_INVALIDATED" in envelope.explanation


def test_a_chain_may_declare_that_an_invalid_component_satisfies_it():
    """The required states are the chain's to declare; HELIOS does not override."""
    package = chain_package(
        strategy_id="proof_declared_invalid",
        primitive="ANY",
        components=[
            component("golden_cross"),
            component("guard_condition", relationship="ANY",
                      required_states=("INVALID",)),
        ],
    )
    envelope = run(
        ChainEngine(package),
        [
            atom("golden_cross", state=StrategyState.DORMANT),
            atom("guard_condition", state=StrategyState.INVALID,
                 direction=Direction.NONE),
        ],
    )
    assert envelope.state is StrategyState.MATCHED


# --------------------------------------------------------- wiring faults


def test_a_component_state_for_another_instrument_fails_loudly():
    with pytest.raises(ContractViolationError) as raised:
        run(
            ChainEngine(simple_chain()),
            [atom("golden_cross"), atom("range_breakout", instrument="EUR_USD")],
        )
    assert "different instrument" in str(raised.value)


def test_two_states_for_one_component_identity_fail_loudly():
    with pytest.raises(ContractViolationError) as raised:
        run(
            ChainEngine(simple_chain()),
            [atom("golden_cross"), atom("golden_cross"), atom("range_breakout")],
        )
    assert "provenance would be ambiguous" in str(raised.value)


def test_undeclared_component_states_are_ignored_not_refused():
    """A runtime may hand the engine every atomic state it has."""
    envelope = run(
        ChainEngine(simple_chain()),
        [atom("golden_cross"), atom("range_breakout"), atom("some_other_atom")],
    )
    assert envelope.state is StrategyState.MATCHED
    assert len(envelope.components) == 2


def test_a_chain_supplied_as_a_component_is_refused_as_out_of_scope_for_v1():
    engine = ChainEngine(simple_chain())
    inner = run(engine, [atom("golden_cross"), atom("range_breakout")])
    assert inner.kind is EnvelopeKind.CHAIN

    outer = chain_package(
        strategy_id="proof_recursive",
        primitive="ALL",
        components=[component("proof_failures"), component("golden_cross")],
    )
    with pytest.raises(StrategySpecError) as raised:
        run(ChainEngine(outer), [inner, atom("golden_cross")])
    assert "explicit architecture authority" in str(raised.value)


def test_a_chain_will_not_resume_from_another_units_envelope():
    engine = ChainEngine(simple_chain())
    mine = run(engine, [atom("golden_cross"), atom("range_breakout")])

    other = ChainEngine(
        chain_package(
            strategy_id="proof_other_chain",
            primitive="ALL",
            components=[component("golden_cross"), component("range_breakout")],
        )
    )
    with pytest.raises(ContractViolationError) as raised:
        run(other, [atom("golden_cross"), atom("range_breakout")], previous=mine)
    assert "resumes only from its own previous envelope" in str(raised.value)


def test_a_chain_will_not_resume_from_an_atomic_envelope():
    with pytest.raises(ContractViolationError) as raised:
        run(
            ChainEngine(simple_chain()),
            [atom("golden_cross"), atom("range_breakout")],
            previous=atom("golden_cross"),
        )
    assert "resumes only from a CHAIN envelope" in str(raised.value)


def test_a_previous_envelope_for_another_instrument_fails_loudly():
    engine = ChainEngine(simple_chain())
    mine = run(engine, [atom("golden_cross"), atom("range_breakout")])
    with pytest.raises(ContractViolationError) as raised:
        engine.evaluate(
            instrument="EUR_USD",
            evaluated_at_utc=at(minutes=5),
            components=[
                atom("golden_cross", instrument="EUR_USD"),
                atom("range_breakout", instrument="EUR_USD"),
            ],
            previous=mine,
        )
    assert "different instrument" in str(raised.value)


def test_an_atomic_package_is_not_a_chain(package_root):
    package = load_strategy_package(package_root / "valid" / "golden_cross.atomic.yaml")
    with pytest.raises(StrategySpecError) as raised:
        ChainEngine(package)
    assert "requires a CHAIN package" in str(raised.value)


def test_a_non_envelope_component_is_refused():
    with pytest.raises(ContractViolationError) as raised:
        run(ChainEngine(simple_chain()), ["golden_cross"])
    assert "must be a StrategyStateEnvelope" in str(raised.value)


def test_a_naive_evaluation_instant_is_refused():
    from datetime import datetime

    with pytest.raises(ContractViolationError):
        ChainEngine(simple_chain()).evaluate(
            instrument=INSTRUMENT,
            evaluated_at_utc=datetime(2026, 1, 5, 12, 0, 0),
            components=[atom("golden_cross"), atom("range_breakout")],
        )


# ------------------------------------------------------------- isolation


def test_one_chains_failure_cannot_reach_another_chain():
    """Two chains sharing components resolve independently."""
    strict = ChainEngine(simple_chain())
    lenient = ChainEngine(
        chain_package(
            strategy_id="proof_lenient",
            primitive="ANY",
            components=[component("golden_cross"), component("range_breakout")],
        )
    )
    states = [atom("golden_cross"), atom("range_breakout", state=StrategyState.INVALID)]
    strict_envelope = run(strict, states)
    lenient_envelope = run(lenient, states)
    assert strict_envelope.state is StrategyState.FORMING
    assert lenient_envelope.state is StrategyState.MATCHED
    # The shared component states were not mutated by either evaluation.
    assert states[0].state is StrategyState.MATCHED
    assert states[1].state is StrategyState.INVALID


def test_a_chain_cannot_mutate_the_component_states_it_was_handed():
    """Isolation is structural: the envelopes handed in are frozen."""
    supplied = atom("golden_cross")
    run(ChainEngine(simple_chain()), [supplied, atom("range_breakout")])
    assert supplied.state is StrategyState.MATCHED
    with pytest.raises(Exception):
        supplied.state = StrategyState.DORMANT
    with pytest.raises(ContractViolationError):
        supplied.evidence["injected"] = 1
