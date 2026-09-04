"""Builders for chain tests, and self-checks that they build valid values.

The chain engine composes **normalised output envelopes**. It has no
dependency on any concrete atomic strategy, so these tests construct component
states directly from the contract layer. That is not a shortcut around real
atoms: it is the decoupling the PID requires, expressed as a test boundary. A
chain test that needed a real atomic strategy would prove the two are coupled.

Packages are built as data and parsed through the real
:func:`~helios.spec.loader.parse_strategy_package`, so every test chain has
passed exactly the validation HSA's own packages pass.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

from helios.clock import from_iso8601_utc
from helios.contracts.output import (
    EnvelopeKind,
    InputFreshness,
    StrategyStateEnvelope,
    Validity,
)
from helios.contracts.state import LIVE_STATES, Direction, StrategyState
from helios.spec.loader import parse_strategy_package
from helios.spec.model import StrategyPackage, STRATEGY_PACKAGE_SCHEMA_VERSION

INSTRUMENT = "XAU_USD"
FACT_SCHEMA_VERSION = "hermes.market_fact/1.0.0"

#: A fixed evaluation instant. Every temporal assertion is relative to it, so
#: no test depends on the wall clock.
T0 = from_iso8601_utc("2026-01-05T12:00:00Z")


def at(seconds: int = 0, *, minutes: int = 0, hours: int = 0) -> datetime:
    """An instant offset from ``T0``. Negative values are in the past."""
    return T0 + timedelta(seconds=seconds, minutes=minutes, hours=hours)


def fact(
    *,
    timeframe: str = "H4",
    role: Optional[str] = None,
    frame_timestamp_utc: Optional[datetime] = None,
    is_fresh: bool = True,
    is_complete: bool = True,
    age_seconds: int = 60,
    max_age_seconds: int = 18000,
    instrument: str = INSTRUMENT,
) -> InputFreshness:
    """One published freshness record behind a component's state."""
    return InputFreshness(
        instrument=instrument,
        timeframe=timeframe,
        semantic_role=role,
        frame_timestamp_utc=frame_timestamp_utc or at(hours=-4),
        age_seconds=age_seconds,
        max_age_seconds=max_age_seconds,
        is_fresh=is_fresh,
        is_complete=is_complete,
        source="hermes",
        schema_version=FACT_SCHEMA_VERSION,
    )


def atom(
    strategy_id: str,
    *,
    version: str = "1.0.0",
    state: StrategyState = StrategyState.MATCHED,
    direction: Direction = Direction.LONG,
    timeframe: str = "H4",
    role: Optional[str] = None,
    matched_at: Optional[datetime] = None,
    last_matched_at: Optional[datetime] = None,
    active_since: Optional[datetime] = None,
    evaluated_at: Optional[datetime] = None,
    valid_from: Optional[datetime] = None,
    valid_until: Optional[datetime] = None,
    validity_reason: Optional[str] = None,
    inputs: Optional[Sequence[InputFreshness]] = None,
    instrument: str = INSTRUMENT,
    strength: Optional[str] = None,
    explanation: Optional[str] = None,
) -> StrategyStateEnvelope:
    """One atomic strategy's published state — the chain engine's only input."""
    evaluated = evaluated_at or T0
    first = matched_at
    if state in LIVE_STATES and first is None:
        first = evaluated
    last = last_matched_at if last_matched_at is not None else first
    since = active_since
    if state in LIVE_STATES and since is None:
        since = first
    if state is StrategyState.DORMANT:
        first = last = since = None
    return StrategyStateEnvelope(
        kind=EnvelopeKind.ATOMIC,
        strategy_id=strategy_id,
        strategy_version=version,
        instrument=instrument,
        timeframe=timeframe,
        semantic_role=role,
        state=state,
        direction=direction,
        strength=strength,
        explanation=explanation,
        first_matched_at_utc=first,
        last_matched_at_utc=last,
        active_since_utc=since,
        last_evaluated_at_utc=evaluated,
        validity=Validity(
            valid_from_utc=valid_from if valid_from is not None else first,
            valid_until_utc=valid_until,
            reason=validity_reason,
        ),
        inputs=tuple(inputs) if inputs is not None else (fact(timeframe=timeframe, role=role),),
    )


def component(
    strategy_id: str,
    *,
    version: str = "1.0.0",
    role: Optional[str] = None,
    sequence_index: Optional[int] = None,
    relationship: str = "SAME",
    required_states: Sequence[str] = ("MATCHED", "ACTIVE", "WEAKENING"),
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "direction_relationship": relationship,
        "required_states": list(required_states),
    }
    if role is not None:
        entry["role"] = role
    if sequence_index is not None:
        entry["sequence_index"] = sequence_index
    return entry


def chain_package(
    *,
    strategy_id: str,
    primitive: str,
    components: Iterable[dict[str, Any]],
    role_timeframes: Optional[dict[str, str]] = None,
    mode: str = "DIRECTIONAL",
    relationship: str = "SAME",
    min_matched_frames: int = 1,
    weakening_enabled: bool = True,
    expiry: Optional[dict[str, Any]] = None,
    ordering_window_seconds: Optional[int] = None,
    version: str = "1.0.0",
) -> StrategyPackage:
    """Build and validate a chain package exactly as HSA would supply one."""
    chain: dict[str, Any] = {
        "primitive": primitive,
        "explanation_required": True,
        "components": list(components),
    }
    if ordering_window_seconds is not None:
        chain["ordering_window_seconds"] = ordering_window_seconds
    document = {
        "schema_version": STRATEGY_PACKAGE_SCHEMA_VERSION,
        "kind": "CHAIN",
        "identity": {"strategy_id": strategy_id, "strategy_version": version},
        "metadata": {
            "title": f"chain {strategy_id}",
            "description": "a chain built for the composition suite",
            "authored_by": "HSA",
            "authored_at_utc": "2026-01-02T09:00:00Z",
        },
        "inputs": [
            {
                "role": role,
                "timeframe": timeframe,
                "lookback": 2,
                "required_fields": ["close"],
            }
            for role, timeframe in (role_timeframes or {}).items()
        ],
        "parameters": {},
        "direction": {
            "mode": mode,
            "resolution": "FROM_COMPONENTS",
            "component_relationship": relationship,
        },
        "timing": {"evaluate_on": "CLOSED_FRAME"},
        "persistence": {
            "min_matched_frames": min_matched_frames,
            "weakening_enabled": weakening_enabled,
        },
        "expiry": expiry or {"mode": "NEVER"},
        "chain": chain,
    }
    return parse_strategy_package(document, origin=f"<{strategy_id}>")


# --------------------------------------------------------------------- checks


def test_the_builders_produce_contract_valid_values():
    envelope = atom("golden_cross")
    assert envelope.kind is EnvelopeKind.ATOMIC
    assert envelope.state is StrategyState.MATCHED
    assert envelope.first_matched_at_utc == T0
    assert envelope.inputs[0].is_fresh is True


def test_a_dormant_component_carries_no_match_history():
    envelope = atom("golden_cross", state=StrategyState.DORMANT)
    assert envelope.first_matched_at_utc is None
    assert envelope.active_since_utc is None


def test_the_package_builder_passes_real_package_validation():
    package = chain_package(
        strategy_id="proof_all",
        primitive="ALL",
        components=[component("golden_cross"), component("range_breakout")],
    )
    assert package.chain is not None
    assert len(package.chain.components) == 2
