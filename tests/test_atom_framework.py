"""The atomic strategy framework: binding, parameters, freshness, state model.

What is proven here is everything an atom does NOT have to implement itself,
because it is identical for every strategy: how a package is bound to an
implementation, how parameters are checked, how missing and stale facts are
refused, and how a verdict becomes a state that respects the documented legal
transition table.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from helios.contracts import (
    Direction,
    EnvelopeKind,
    FreshnessPolicy,
    MarketFactWindow,
    StrategyState,
    Timeframe,
    is_legal_transition,
)
from helios.errors import ContractViolationError, MissingFactError, StaleFactError, StrategySpecError
from helios.spec import load_strategy_package, parse_strategy_package
from helios.strategies import AtomRegistry
from helios.strategies.base import AtomicStrategy
from helios.strategies.catalogue import ATOM_TYPES, default_registry
from tests.conftest import make_frame
from tests.test_atom_support import (
    HSA_PACKAGES,
    PACKAGE_PATHS,
    at,
    build,
    context_for,
    fixture_window,
    package_for,
    package_variant,
    read_package_document,
    replay,
    states,
)

# ------------------------------------------------------------------- binding


def test_every_reference_package_binds_to_its_implementation():
    registry = default_registry()
    for name in PACKAGE_PATHS:
        atom = registry.build(package_for(name))
        assert isinstance(atom, AtomicStrategy)
        assert str(atom.identity.strategy_id) == name


def test_the_binding_key_is_the_package_identity():
    """There is no separate implementation field to disagree with identity."""
    for atom_type in ATOM_TYPES:
        assert atom_type.ATOM_NAME
        package = package_for(atom_type.ATOM_NAME)
        assert str(package.identity.strategy_id) == atom_type.ATOM_NAME


def test_a_package_naming_an_unknown_atom_is_refused():
    document = read_package_document(PACKAGE_PATHS["golden_cross"])
    document["identity"]["strategy_id"] = "unheard_of_condition"
    package = parse_strategy_package(document, origin="unknown atom")
    with pytest.raises(StrategySpecError) as failure:
        default_registry().build(package)
    assert failure.value.context["atom"] == "unheard_of_condition"
    assert "golden_cross" in failure.value.context["registered"]


def test_a_chain_package_is_not_an_atomic_strategy():
    chain = load_strategy_package(HSA_PACKAGES / "gold_context_trigger.chain.yaml")
    with pytest.raises(StrategySpecError) as failure:
        default_registry().build(chain)
    assert failure.value.context["kind"] == "CHAIN"


def test_a_package_declaring_two_inputs_is_refused():
    document = read_package_document(PACKAGE_PATHS["golden_cross"])
    document["identity"]["strategy_version"] = "1.1.0"
    document["inputs"].append(
        {"role": "TRIGGER", "timeframe": "M5", "lookback": 2,
         "required_fields": ["close"]}
    )
    package = parse_strategy_package(document, origin="two inputs")
    with pytest.raises(StrategySpecError) as failure:
        default_registry().build(package)
    assert failure.value.context["declared_inputs"] == 2


def test_a_package_that_does_not_declare_a_fact_the_atom_reads_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "golden_cross",
                **{"identity.strategy_version": "1.1.0",
                   "inputs.0.required_fields": ["close", "ema_50"]},
            )
        )
    assert failure.value.context["missing"] == ["ema_200"]


def test_a_package_declaring_less_history_than_the_atom_needs_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "golden_cross",
                **{"identity.strategy_version": "1.1.0", "inputs.0.lookback": 1},
            )
        )
    assert failure.value.context["declared_lookback"] == 1
    assert failure.value.context["minimum_lookback"] == 2


# ---------------------------------------------------------------- parameters


def test_a_misspelt_parameter_never_becomes_a_silent_no_op():
    document = read_package_document(PACKAGE_PATHS["golden_cross"])
    document["identity"]["strategy_version"] = "1.1.0"
    document["parameters"]["min_seperation"] = {
        "type": "DECIMAL", "value": Decimal("0.25"), "minimum": 0, "maximum": 100
    }
    package = parse_strategy_package(document, origin="typo")
    with pytest.raises(StrategySpecError) as failure:
        default_registry().build(package)
    assert failure.value.context["unknown"] == ["min_seperation"]


def test_a_parameter_the_atom_needs_and_the_package_omits_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "golden_cross",
                **{"identity.strategy_version": "1.1.0", "parameters": {}},
            )
        )
    assert failure.value.context["parameter"] == "min_separation"


def test_a_parameter_declared_with_the_wrong_type_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "golden_cross",
                **{"identity.strategy_version": "1.1.0",
                   "parameters.min_separation.type": "INTEGER",
                   "parameters.min_separation.value": 1},
            )
        )
    assert failure.value.context["expected"] == "DECIMAL"
    assert failure.value.context["declared"] == "INTEGER"


def test_a_value_below_the_atoms_own_minimum_is_refused():
    """The package's own range is wide enough; the ATOM's is not."""
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "momentum_volatility",
                **{"identity.strategy_version": "1.1.0",
                   "parameters.min_volatility_expansion.minimum": 0,
                   "parameters.min_volatility_expansion.value": Decimal("0.5")},
            )
        )
    assert failure.value.context["parameter"] == "min_volatility_expansion"
    assert failure.value.context["minimum"] == "1"


def test_a_value_above_the_atoms_own_maximum_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "momentum_volatility",
                **{"identity.strategy_version": "1.1.0",
                   "parameters.momentum_upper.maximum": 200,
                   "parameters.momentum_upper.value": Decimal("150.00")},
            )
        )
    assert failure.value.context["parameter"] == "momentum_upper"
    assert failure.value.context["maximum"] == "100"


def test_a_value_outside_the_packages_own_range_never_reaches_the_atom():
    """The package format refuses it first; the loud failure is not deferred."""
    with pytest.raises(StrategySpecError):
        package_variant(
            "golden_cross",
            **{"identity.strategy_version": "1.1.0",
               "parameters.min_separation.value": Decimal("500")},
        )


def test_decimal_parameters_stay_exact_decimals():
    atom = build(package_for("rejection_wick"))
    value = atom.parameters["min_wick_ratio"]
    assert isinstance(value, Decimal)
    assert value == Decimal("0.6")


# ------------------------------------------------------------------ registry


def test_two_implementations_cannot_claim_one_name():
    class Impostor(AtomicStrategy):
        ATOM_NAME = "golden_cross"

    registry = default_registry()
    with pytest.raises(StrategySpecError) as failure:
        registry.register(Impostor)
    assert failure.value.context["atom"] == "golden_cross"


def test_registering_the_same_implementation_twice_is_harmless():
    registry = default_registry()
    for atom_type in ATOM_TYPES:
        registry.register(atom_type)
    assert registry.registered == tuple(sorted(item.ATOM_NAME for item in ATOM_TYPES))


def test_only_an_atomic_strategy_can_be_registered():
    class NotAStrategy:
        ATOM_NAME = "whatever"

    with pytest.raises(StrategySpecError):
        AtomRegistry().register(NotAStrategy)


def test_an_implementation_without_a_name_is_refused():
    class Nameless(AtomicStrategy):
        pass

    with pytest.raises(StrategySpecError):
        AtomRegistry().register(Nameless)


def test_build_all_is_ordered_by_identity_not_by_iteration():
    registry = default_registry()
    packages = [package_for(name) for name in PACKAGE_PATHS]
    forwards = registry.build_all(packages)
    backwards = registry.build_all(list(reversed(packages)))
    assert [atom.identity.canonical for atom in forwards] == [
        atom.identity.canonical for atom in backwards
    ]
    assert [atom.identity.canonical for atom in forwards] == sorted(
        atom.identity.canonical for atom in forwards
    )


# ------------------------------------------------- role and timeframe binding


def test_the_role_and_timeframe_come_from_the_package():
    reference = build(package_for("golden_cross"))
    assert (str(reference.role), reference.timeframe) == ("CONTEXT", Timeframe.H4)
    rebound = build(
        package_variant(
            "golden_cross",
            **{"identity.strategy_version": "2.0.0", "inputs.0.role": "BIAS",
               "inputs.0.timeframe": "D1"},
        )
    )
    assert (str(rebound.role), rebound.timeframe) == ("BIAS", Timeframe.D1)
    assert type(reference) is type(rebound)


def test_required_inputs_mirror_the_package():
    atom = build(package_for("range_breakout"))
    declared = atom.required_inputs()
    assert len(declared) == 1
    assert str(declared[0].role) == "TRIGGER"
    assert declared[0].timeframe is Timeframe.M5
    assert declared[0].lookback == 20
    assert declared[0].fields == ("high", "low", "close", "atr_14")


def test_a_window_on_the_wrong_timeframe_is_refused(policy):
    """An H4 strategy must never silently evaluate M15 facts."""
    atom = build(package_for("golden_cross"))
    _, window = fixture_window("xau_usd_m15")
    context = context_for(
        atom, window, policy,
        instrument=window.instrument,
        evaluated_at_utc=window.latest.close_time_utc,
    )
    with pytest.raises(ContractViolationError) as failure:
        atom.evaluate(context)
    assert failure.value.context["declared"] == "H4"
    assert failure.value.context["received"] == "M15"


def test_a_window_for_another_instrument_is_refused(policy):
    atom = build(package_for("golden_cross"))
    frames = [
        make_frame(instrument="EUR_USD", timestamp_utc=f"2026-08-01T{4 * i:02d}:00:00Z",
                   indicators={"ema_50": "2401.00", "ema_200": "2400.00"})
        for i in range(2)
    ]
    window = MarketFactWindow(frames)
    context = context_for(
        atom, window, policy,
        instrument=make_frame().instrument,
        evaluated_at_utc=frames[-1].close_time_utc,
    )
    with pytest.raises(ContractViolationError) as failure:
        atom.evaluate(context)
    assert failure.value.context["received"] == "EUR_USD"


def test_an_unbound_role_fails_loudly(policy):
    atom = build(package_for("golden_cross"))
    _, window = fixture_window("xau_usd_h4")
    from helios.protocols import EvaluationContext

    context = EvaluationContext(
        instrument=window.instrument,
        evaluated_at_utc=window.latest.close_time_utc,
        windows={},
        parameters=atom.parameters,
        freshness_policy=policy,
    )
    with pytest.raises(MissingFactError) as failure:
        atom.evaluate(context)
    assert failure.value.context["role"] == "CONTEXT"


def test_insufficient_history_fails_loudly_rather_than_evaluating_short(policy):
    """A strategy declaring 20 bars must not quietly evaluate against 3."""
    atom = build(package_for("range_breakout"))
    _, window = fixture_window("xau_usd_m5")
    short = MarketFactWindow(window.frames[:3])
    context = context_for(
        atom, short, policy,
        instrument=short.instrument, evaluated_at_utc=short.latest.close_time_utc,
    )
    with pytest.raises(MissingFactError) as failure:
        atom.evaluate(context)
    assert failure.value.context["requested"] == 20
    assert failure.value.context["available"] == 3


# ------------------------------------------------------------------ freshness


def test_stale_facts_are_refused_by_the_configured_policy(policy):
    atom = build(package_for("golden_cross"))
    fixture, window = fixture_window("xau_usd_h4_stale", stale=True)
    context = context_for(
        atom, window, policy,
        instrument=fixture.instrument, evaluated_at_utc=fixture.reference_now_utc,
    )
    with pytest.raises(StaleFactError) as failure:
        atom.evaluate(context)
    assert failure.value.context["timeframe"] == "H4"
    assert failure.value.context["age_seconds"] > failure.value.context["max_age_seconds"]


def test_a_forming_bar_is_refused_when_configuration_forbids_it(policy):
    atom = build(package_for("golden_cross"))
    fixture, window = fixture_window("xau_usd_h4_incomplete_last_bar", stale=True)
    context = context_for(
        atom, window, policy,
        instrument=fixture.instrument, evaluated_at_utc=fixture.reference_now_utc,
    )
    with pytest.raises(StaleFactError) as failure:
        atom.evaluate(context)
    assert "incomplete bar" in failure.value.message


def test_a_package_may_be_stricter_than_configuration_but_never_laxer(policy):
    """A declared max_age_seconds tightens the limit; it cannot relax it."""
    tightened = build(
        package_variant(
            "golden_cross",
            **{"identity.strategy_version": "1.1.0", "inputs.0.max_age_seconds": 60},
        )
    )
    fixture, window = fixture_window("xau_usd_h4")
    # The canonical fixture is judged 60 seconds after its last bar closed.
    ok = context_for(
        tightened, window, policy,
        instrument=fixture.instrument, evaluated_at_utc=fixture.reference_now_utc,
    )
    assert tightened.evaluate(ok).inputs[0].max_age_seconds == 60

    late = context_for(
        tightened, window, policy,
        instrument=fixture.instrument,
        evaluated_at_utc=fixture.reference_now_utc + timedelta(seconds=61),
    )
    with pytest.raises(StaleFactError):
        tightened.evaluate(late)

    # The same facts at the same instant are fresh under the deployment's own
    # limit, so the refusal above came from the package, not the configuration.
    baseline = build(package_for("golden_cross"))
    relaxed = context_for(
        baseline, window, policy,
        instrument=fixture.instrument,
        evaluated_at_utc=fixture.reference_now_utc + timedelta(seconds=61),
    )
    assert baseline.evaluate(relaxed).inputs[0].is_fresh


def test_a_closed_frame_package_refuses_a_forming_bar_a_deployment_would_accept():
    """Configuration is the ceiling; the package's timing may only tighten it."""
    permissive = FreshnessPolicy(
        max_age_multiplier="1.5", grace=timedelta(seconds=60),
        allow_incomplete_frames=True,
        clock_skew_tolerance=timedelta(seconds=5),
    )
    atom = build(package_for("golden_cross"))
    fixture, window = fixture_window("xau_usd_h4_incomplete_last_bar", stale=True)
    context = context_for(
        atom, window, permissive,
        instrument=fixture.instrument, evaluated_at_utc=fixture.reference_now_utc,
    )
    with pytest.raises(StaleFactError):
        atom.evaluate(context)

    every_frame = build(
        package_variant(
            "golden_cross",
            **{"identity.strategy_version": "1.1.0",
               "timing.evaluate_on": "EVERY_FRAME"},
        )
    )
    allowed = context_for(
        every_frame, window, permissive,
        instrument=fixture.instrument, evaluated_at_utc=fixture.reference_now_utc,
    )
    envelope = every_frame.evaluate(allowed)
    assert envelope.inputs[0].is_complete is False


def test_published_freshness_describes_the_facts_behind_the_state(policy):
    atom = build(package_for("golden_cross"))
    envelope = at(replay(atom, "xau_usd_h4", policy), 6)
    assert len(envelope.inputs) == 1
    published = envelope.inputs[0]
    assert published.timeframe is Timeframe.H4
    assert str(published.semantic_role) == "CONTEXT"
    assert published.is_fresh is True
    assert published.is_complete is True
    assert str(published.source) == "hermes"


# ---------------------------------------------------------------- state model


def test_every_published_transition_is_legal(policy):
    """The state machine can never propose something the table forbids."""
    for name in PACKAGE_PATHS:
        for fixture_name in ("xau_usd_h4", "xau_usd_h1", "xau_usd_m15", "xau_usd_m5"):
            atom = build(package_for(name))
            if atom.timeframe is not Timeframe.parse(fixture_name.rsplit("_", 1)[-1]):
                continue
            results = replay(atom, fixture_name, policy)
            published = [envelope.state for _, envelope in results]
            for previous, current in zip(published, published[1:]):
                assert is_legal_transition(previous, current), (
                    f"{name}: {previous} -> {current}"
                )


def test_an_expired_occurrence_cannot_restart_while_its_condition_holds(policy):
    """Expiry would mean nothing if the same still-true condition rearmed at once.

    The frame-11 location match is valid for two H1 frames, so it ages out at
    frame 14 (15:00, past the 14:00 limit). Frames 15-20 are still within the
    declared distance of a swing low in the same direction, so the occurrence
    stays EXPIRED instead of re-matching what just aged out. It rearms at
    frame 21, where the close is exactly equidistant from both extremes and
    the condition therefore stops holding.
    """
    atom = build(
        package_variant(
            "swing_proximity",
            **{"identity.strategy_version": "1.6.0", "inputs.0.lookback": 12,
               "parameters.lookback_bars.value": 12, "expiry.frames": 2},
        )
    )
    results = replay(atom, "xau_usd_h1", policy)
    assert states(results) == [
        "MATCHED", "ACTIVE", "ACTIVE",              # frames 11-13
        "EXPIRED", "EXPIRED", "EXPIRED", "EXPIRED", # frames 14-17
        "EXPIRED", "EXPIRED", "EXPIRED",            # frames 18-20
        "DORMANT",                                  # frame 21: condition cleared
        "MATCHED", "ACTIVE",                        # frames 22-23: a new occurrence
    ]
    still_expired = at(results, 20)
    assert still_expired.direction is Direction.SHORT
    assert "cannot restart until the condition clears" in still_expired.explanation
    assert at(results, 21).state is StrategyState.DORMANT
    assert at(results, 14).validity.reason is not None


def test_persistence_delays_the_match_by_the_declared_frames(policy):
    """min_matched_frames 2 turns the match edge into a FORMING bar first."""
    atom = build(
        package_variant(
            "swing_proximity",
            **{"identity.strategy_version": "1.7.0", "inputs.0.lookback": 12,
               "parameters.lookback_bars.value": 12,
               "persistence.min_matched_frames": 2},
        )
    )
    results = replay(atom, "xau_usd_h1", policy)
    assert states(results)[:3] == ["FORMING", "MATCHED", "ACTIVE"]
    assert at(results, 11).evidence["consecutive_hold_frames"] == 1
    assert at(results, 12).evidence["consecutive_hold_frames"] == 2


def test_an_edge_condition_may_not_be_asked_to_persist_before_matching():
    """A package that could never match is a specification error, not a silence."""
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "golden_cross",
                **{"identity.strategy_version": "1.1.0",
                   "persistence.min_matched_frames": 3},
            )
        )
    assert failure.value.context["min_matched_frames"] == 3
    assert "could never match" in failure.value.message


def test_a_dormant_envelope_carries_no_match_history(policy):
    atom = build(package_for("golden_cross"))
    envelope = at(replay(atom, "xau_usd_h4", policy), 1)
    assert envelope.state is StrategyState.DORMANT
    assert envelope.first_matched_at_utc is None
    assert envelope.last_matched_at_utc is None
    assert envelope.active_since_utc is None
    assert envelope.validity.valid_from_utc is None


def test_every_envelope_is_atomic_and_carries_no_chain_identity(policy):
    atom = build(package_for("golden_cross"))
    for _, envelope in replay(atom, "xau_usd_h4", policy):
        assert envelope.kind is EnvelopeKind.ATOMIC
        assert envelope.chain_id is None
        assert envelope.chain_version is None
        assert envelope.components == ()
        assert envelope.explanation


def test_a_strategy_is_never_handed_another_strategys_envelope(policy):
    """Handing a cross's state to a wick strategy is a loud failure."""
    cross = build(package_for("golden_cross"))
    wick = build(package_for("rejection_wick"))
    published = at(replay(cross, "xau_usd_h4", policy), 6)
    _, window = fixture_window("xau_usd_m15")
    context = context_for(
        wick, window, policy,
        instrument=window.instrument,
        evaluated_at_utc=window.latest.close_time_utc,
        previous=published,
    )
    with pytest.raises(ContractViolationError) as failure:
        wick.evaluate(context)
    assert failure.value.context["received"] == "golden_cross@1.0.0"
