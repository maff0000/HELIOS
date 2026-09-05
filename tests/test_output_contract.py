"""The normalised output contract FALCON consumes."""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import (
    CER_OWNED_IDENTITY_FIELDS,
    ComponentProvenance,
    Direction,
    EnvelopeKind,
    InputFreshness,
    StrategyState,
    StrategyStateEnvelope,
    Validity,
    advance_lifecycle,
    assess_frame,
)
from helios.contracts.output import ENVELOPE_FIELDS, ENVELOPE_SCHEMA_VERSION
from helios.errors import ContractViolationError, SerialisationError
from tests.conftest import make_frame, utc

#: Every field the PID's "Normalised output contract" section names.
PID_REQUIRED_FIELDS = (
    "strategy_id",
    "strategy_version",
    "chain_id",
    "chain_version",
    "instrument",
    "timeframe",
    "semantic_role",
    "state",
    "direction",
    "strength",
    "evidence",
    "first_matched_at_utc",
    "last_matched_at_utc",
    "active_since_utc",
    "last_evaluated_at_utc",
    "validity",
    "components",
    "schema_version",
    "inputs",
)


def freshness_input(policy, *, role="CONTEXT"):
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-05T04:01:00Z"))
    return InputFreshness.from_verdict(verdict, semantic_role=role)


def lifecycle_fields(state, moment="2026-01-05T04:00:00Z"):
    """Lifecycle instants as flat fields, so a test may override just one."""
    lifecycle = advance_lifecycle(None, state, utc(moment))
    return dict(
        first_matched_at_utc=lifecycle.first_matched_at_utc,
        last_matched_at_utc=lifecycle.last_matched_at_utc,
        active_since_utc=lifecycle.active_since_utc,
        last_evaluated_at_utc=lifecycle.last_evaluated_at_utc,
    )


def atomic(policy, *, state=StrategyState.MATCHED, **overrides):
    fields = dict(
        kind=EnvelopeKind.ATOMIC,
        strategy_id="golden_cross",
        strategy_version="1.0.0",
        instrument="XAU_USD",
        timeframe="H4",
        semantic_role="CONTEXT",
        state=state,
        direction=Direction.LONG,
        strength="0.75",
        evidence={"ema_50": "2400.40", "ema_200": "2400.00", "bars_since_cross": 1},
        explanation="ema_50 crossed above ema_200",
        validity=Validity(valid_from_utc=utc("2026-01-05T04:00:00Z"), valid_until_utc=None),
        inputs=(freshness_input(policy),),
        **lifecycle_fields(state),
    )
    fields.update(overrides)
    return StrategyStateEnvelope(**fields)


def component(**overrides):
    fields = dict(
        strategy_id="golden_cross",
        strategy_version="1.0.0",
        state=StrategyState.ACTIVE,
        direction=Direction.LONG,
        timeframe="H4",
        semantic_role="CONTEXT",
        matched=True,
        last_evaluated_at_utc=utc("2026-01-05T04:00:00Z"),
        contribution="context agrees",
    )
    fields.update(overrides)
    return ComponentProvenance(**fields)


def chain(policy, **overrides):
    fields = dict(
        kind=EnvelopeKind.CHAIN,
        strategy_id="gold_context_trigger",
        strategy_version="1.0.0",
        chain_id="gold_context_trigger",
        chain_version="1.0.0",
        instrument="XAU_USD",
        timeframe=None,
        semantic_role=None,
        state=StrategyState.MATCHED,
        direction=Direction.LONG,
        strength="0.80",
        evidence={},
        explanation="CONTEXT and TRIGGER both LONG within the window",
        validity=Validity(valid_from_utc=utc("2026-01-05T04:00:00Z")),
        components=(
            component(),
            component(
                strategy_id="range_breakout",
                semantic_role="TRIGGER",
                timeframe="M5",
                state=StrategyState.MATCHED,
                contribution="breakout above the range high",
            ),
        ),
        inputs=(freshness_input(policy), freshness_input(policy, role="TRIGGER")),
        **lifecycle_fields(StrategyState.MATCHED),
    )
    fields.update(overrides)
    return StrategyStateEnvelope(**fields)


# --------------------------------------------------------------- completeness


def test_envelope_carries_every_field_the_pid_requires():
    missing = [name for name in PID_REQUIRED_FIELDS if name not in ENVELOPE_FIELDS]
    assert missing == []


def test_one_envelope_serves_atomic_and_chain(policy):
    assert type(atomic(policy)) is type(chain(policy))
    assert set(atomic(policy).to_canonical_dict()) == set(chain(policy).to_canonical_dict())


def test_falcon_reads_identity_uniformly_without_branching(policy):
    """A consumer must not need to know the kind to read who published."""
    for envelope in (atomic(policy), chain(policy)):
        document = envelope.to_canonical_dict()
        assert document["strategy_id"]
        assert document["strategy_version"]
        assert document["kind"] in ("ATOMIC", "CHAIN")


def test_chain_identity_is_published_under_both_names(policy):
    document = chain(policy).to_canonical_dict()
    assert document["chain_id"] == document["strategy_id"]
    assert document["chain_version"] == document["strategy_version"]


def test_helios_never_publishes_cer_owned_evidence_identity(policy):
    """HELIOS is not an evidence store; CER mints those identifiers."""
    for envelope in (atomic(policy), chain(policy)):
        document = envelope.to_canonical_dict()
        for name in CER_OWNED_IDENTITY_FIELDS:
            assert name not in document
        assert name not in ENVELOPE_FIELDS


# ------------------------------------------------------------------ coherence


def test_an_atomic_envelope_refuses_chain_identity(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, chain_id="golden_cross", chain_version="1.0.0")


def test_an_atomic_envelope_refuses_components(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, components=(component(),))


def test_an_atomic_envelope_must_state_where_it_looked(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, timeframe=None, semantic_role=None)


def test_a_chain_envelope_requires_chain_identity(policy):
    with pytest.raises(ContractViolationError):
        chain(policy, chain_id=None, chain_version=None)


def test_a_chain_envelope_requires_component_provenance(policy):
    with pytest.raises(ContractViolationError):
        chain(policy, components=())


def test_chain_identity_must_agree_with_strategy_identity(policy):
    with pytest.raises(ContractViolationError):
        chain(policy, chain_id="some_other_chain")


def test_a_component_flag_cannot_contradict_its_state():
    with pytest.raises(ContractViolationError):
        component(state=StrategyState.DORMANT, matched=True)
    with pytest.raises(ContractViolationError):
        component(state=StrategyState.ACTIVE, matched=False)


def test_a_live_state_must_publish_its_lifecycle(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, first_matched_at_utc=None)


def test_a_dormant_state_carries_no_match_history(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, state=StrategyState.DORMANT,
               last_matched_at_utc=utc("2026-01-05T04:00:00Z"))


def test_lifecycle_instants_cannot_postdate_the_evaluation(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, first_matched_at_utc=utc("2026-01-06T00:00:00Z"))


def test_strength_stays_within_the_unit_interval(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, strength="1.5")
    with pytest.raises(ContractViolationError):
        atomic(policy, strength="-0.1")


def test_evidence_holds_scalars_only(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, evidence={"nested": {"not": "allowed"}})
    with pytest.raises(ContractViolationError):
        atomic(policy, evidence={"lossy": 0.1})


def test_an_unknown_schema_version_is_refused(policy):
    with pytest.raises(ContractViolationError):
        atomic(policy, schema_version="helios.strategy_state/9.9.9")


def test_a_validity_window_cannot_end_before_it_starts():
    with pytest.raises(ContractViolationError):
        Validity(
            valid_from_utc=utc("2026-01-05T04:00:00Z"),
            valid_until_utc=utc("2026-01-05T00:00:00Z"),
        )


# --------------------------------------------------------------- immutability


def test_a_published_envelope_cannot_be_edited(policy):
    envelope = atomic(policy)
    with pytest.raises(Exception):
        envelope.state = StrategyState.INVALID  # type: ignore[misc]
    with pytest.raises(ContractViolationError):
        envelope.evidence["injected"] = "value"  # type: ignore[index]
    with pytest.raises(Exception):
        envelope.components.append(component())  # type: ignore[attr-defined]


# ------------------------------------------------------------- serialisation


def test_serialisation_round_trips_exactly(policy):
    for envelope in (atomic(policy), chain(policy)):
        text = envelope.to_canonical_json()
        restored = StrategyStateEnvelope.from_canonical_json(text)
        assert restored == envelope
        assert restored.to_canonical_json() == text


def test_serialisation_is_byte_stable_across_construction_order(policy):
    first = atomic(policy, evidence={"a": 1, "b": "2", "c": True})
    second = atomic(policy, evidence={"c": True, "b": "2", "a": 1})
    assert first.to_canonical_json() == second.to_canonical_json()


def test_serialisation_keeps_absent_facts_visible(policy):
    document = atomic(policy, strength=None).to_canonical_dict()
    assert "strength" in document
    assert document["strength"] is None


def test_decimals_survive_serialisation_without_float_error(policy):
    envelope = atomic(policy, strength="0.1")
    restored = StrategyStateEnvelope.from_canonical_json(envelope.to_canonical_json())
    assert restored.strength == Decimal("0.1")
    assert isinstance(restored.strength, Decimal)


def test_instants_serialise_as_fixed_width_utc(policy):
    document = atomic(policy).to_canonical_dict()
    assert document["last_evaluated_at_utc"] == "2026-01-05T04:00:00.000000Z"
    for name in ("first_matched_at_utc", "last_matched_at_utc", "active_since_utc"):
        assert document[name].endswith("Z")


def test_malformed_published_json_is_refused():
    with pytest.raises(SerialisationError):
        StrategyStateEnvelope.from_canonical_json("{not json")
    with pytest.raises(ContractViolationError):
        StrategyStateEnvelope.from_canonical_json('{"kind":"ATOMIC"}')


def test_schema_version_is_published(policy):
    assert atomic(policy).to_canonical_dict()["schema_version"] == ENVELOPE_SCHEMA_VERSION


def test_with_lifecycle_builds_the_same_envelope(policy):
    """The convenience constructor must not diverge from explicit fields."""
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, utc("2026-01-05T04:00:00Z"))
    built = StrategyStateEnvelope.with_lifecycle(
        lifecycle,
        kind=EnvelopeKind.ATOMIC,
        strategy_id="golden_cross",
        strategy_version="1.0.0",
        instrument="XAU_USD",
        timeframe="H4",
        semantic_role="CONTEXT",
        state=StrategyState.MATCHED,
        direction=Direction.LONG,
        strength="0.75",
        evidence={"ema_50": "2400.40", "ema_200": "2400.00", "bars_since_cross": 1},
        explanation="ema_50 crossed above ema_200",
        validity=Validity(valid_from_utc=utc("2026-01-05T04:00:00Z"), valid_until_utc=None),
        inputs=(freshness_input(policy),),
    )
    assert built == atomic(policy)
