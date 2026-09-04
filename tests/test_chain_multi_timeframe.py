"""Multi-timeframe semantics, proven without a global role-to-timeframe rule.

The PID's GOLD template — CONTEXT 4H, LOCATION 1H, CONFIRMATION 15M, TRIGGER
5M — is a *strategy-engineering template*, not a HELIOS rule. These tests prove
it twice over:

* the checked-in GOLD chain packages evaluate correctly on that mapping;
* a chain with a different mapping and a different number of stages evaluates
  identically well, with no code change;

and then prove it mechanically: no timeframe code appears anywhere in the
composition engine's source.
"""

from __future__ import annotations

from helios.composition import ChainEngine
from helios.contracts.state import Direction, StrategyState
from helios.contracts.timeframe import ALL_TIMEFRAMES
from helios.spec.loader import load_strategy_package
from tests._scan import code_tokens, python_files
from tests.test_chain_support import (
    INSTRUMENT,
    T0,
    at,
    atom,
    chain_package,
    component,
)


def run(engine, components, now=None, previous=None):
    return engine.evaluate(
        instrument=INSTRUMENT,
        evaluated_at_utc=now or T0,
        components=components,
        previous=previous,
    )


# ------------------------------------------------- the GOLD template packages


def test_the_checked_in_gold_context_trigger_chain_evaluates_on_4h_and_5m(package_root):
    package = load_strategy_package(
        package_root / "valid" / "gold_context_trigger.chain.yaml"
    )
    engine = ChainEngine(package)
    assert package.role_timeframes["CONTEXT"].code == "H4"
    assert package.role_timeframes["TRIGGER"].code == "M5"

    envelope = run(
        engine,
        [
            atom("golden_cross", timeframe="H4", role="CONTEXT",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-8)),
            atom("range_breakout", timeframe="M5", role="TRIGGER",
                 state=StrategyState.MATCHED, matched_at=at(minutes=-5)),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG
    published = {
        str(item.semantic_role): item.timeframe.code for item in envelope.components
    }
    assert published == {"CONTEXT": "H4", "TRIGGER": "M5"}


def test_the_checked_in_gold_sequence_chain_evaluates_across_four_stages(package_root):
    package = load_strategy_package(package_root / "valid" / "gold_sequence.chain.yaml")
    engine = ChainEngine(package)
    assert {role: frame.code for role, frame in package.role_timeframes.items()} == {
        "CONTEXT": "H4",
        "LOCATION": "H1",
        "CONFIRMATION": "M15",
        "TRIGGER": "M5",
    }

    envelope = run(
        engine,
        [
            atom("golden_cross", timeframe="H4", role="CONTEXT",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-6)),
            atom("swing_proximity", timeframe="H1", role="LOCATION",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-3)),
            atom("rejection_wick", timeframe="M15", role="CONFIRMATION",
                 state=StrategyState.MATCHED, matched_at=at(minutes=-30)),
            atom("range_breakout", timeframe="M5", role="TRIGGER",
                 state=StrategyState.MATCHED, matched_at=at(minutes=-5)),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert [item.sequence_index for item in envelope.components] == [0, 1, 2, 3]
    assert [item.timeframe.code for item in envelope.components] == [
        "H4",
        "H1",
        "M15",
        "M5",
    ]


# ------------------------------------------------------- a different mapping


def test_a_chain_with_a_different_mapping_and_fewer_stages_behaves_identically():
    """CONTEXT on D1, TRIGGER on M15 — two stages, no GOLD timeframe in sight."""
    package = chain_package(
        strategy_id="daily_context_chain",
        primitive="CONTEXT_TRIGGER",
        components=[
            component("regime_bias", role="CONTEXT"),
            component("rejection_wick", role="TRIGGER", required_states=("MATCHED",)),
        ],
        role_timeframes={"CONTEXT": "D1", "TRIGGER": "M15"},
    )
    envelope = run(
        ChainEngine(package),
        [
            atom("regime_bias", timeframe="D1", role="CONTEXT",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-20)),
            atom("rejection_wick", timeframe="M15", role="TRIGGER",
                 state=StrategyState.MATCHED, matched_at=at(minutes=-15)),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    published = {
        str(item.semantic_role): item.timeframe.code for item in envelope.components
    }
    assert published == {"CONTEXT": "D1", "TRIGGER": "M15"}


def test_a_two_stage_sequence_on_non_template_roles_works_the_same_way():
    package = chain_package(
        strategy_id="two_stage_sequence",
        primitive="SEQUENCE",
        components=[
            component("volatility_squeeze", role="REGIME", sequence_index=0),
            component("range_breakout", role="EXPANSION", sequence_index=1,
                      required_states=("MATCHED",)),
        ],
        role_timeframes={"REGIME": "H1", "EXPANSION": "M1"},
        ordering_window_seconds=7200,
    )
    envelope = run(
        ChainEngine(package),
        [
            atom("volatility_squeeze", timeframe="H1", role="REGIME",
                 state=StrategyState.ACTIVE, matched_at=at(hours=-1)),
            atom("range_breakout", timeframe="M1", role="EXPANSION",
                 state=StrategyState.MATCHED, matched_at=at(minutes=-1)),
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert [str(item.semantic_role) for item in envelope.components] == [
        "REGIME",
        "EXPANSION",
    ]


def test_a_five_stage_sequence_works_with_roles_helios_has_never_seen():
    roles = ("MACRO", "REGIME", "STRUCTURE", "SETUP", "SPARK")
    frames = ("D1", "H4", "H1", "M15", "M5")
    package = chain_package(
        strategy_id="five_stage_sequence",
        primitive="SEQUENCE",
        components=[
            component(f"atom_{index}", role=role, sequence_index=index,
                      required_states=("MATCHED", "ACTIVE"))
            for index, role in enumerate(roles)
        ],
        role_timeframes=dict(zip(roles, frames)),
        ordering_window_seconds=172800,
    )
    envelope = run(
        ChainEngine(package),
        [
            atom(f"atom_{index}", timeframe=frames[index], role=role,
                 state=StrategyState.ACTIVE, matched_at=at(hours=-(5 - index)))
            for index, role in enumerate(roles)
        ],
    )
    assert envelope.state is StrategyState.MATCHED
    assert [str(item.semantic_role) for item in envelope.components] == list(roles)


# ------------------------------------------------- mechanical no-hard-coding


def test_no_timeframe_code_appears_anywhere_in_the_composition_engine(repo_root):
    """The engine cannot prefer a timeframe it is unable to name."""
    codes = set()
    for timeframe in ALL_TIMEFRAMES:
        codes.add(timeframe.code)
        codes.add(timeframe.hermes_code)
    offences = []
    for path in python_files(repo_root / "helios" / "composition"):
        for kind, text in code_tokens(path):
            if text in codes:
                offences.append(f"{path}: {kind} {text!r}")
    assert not offences, offences


def test_the_engine_names_only_the_roles_its_own_primitive_defines(repo_root):
    """CONTEXT and TRIGGER are structural to CONTEXT_TRIGGER; the rest are data.

    LOCATION and CONFIRMATION are stages of the PID's GOLD *template*. If the
    engine named them it would be encoding one strategy-engineering house style
    into the platform.
    """
    template_only = {"LOCATION", "CONFIRMATION"}
    offences = []
    for path in python_files(repo_root / "helios" / "composition"):
        for kind, text in code_tokens(path):
            if text in template_only or text in {f"ROLE_{item}" for item in template_only}:
                offences.append(f"{path}: {kind} {text!r}")
    assert not offences, offences
