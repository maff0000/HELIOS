"""Exemplar published states — the golden contract fixtures FALCON codes against.

An integrator should not have to run HELIOS to find out what it publishes.
These four exemplars cover the shapes a FALCON consumer must handle:

* an **atomic** state that matched;
* a **chain** state that matched, carrying component provenance;
* a **non-match**, which still publishes an explanation of why nothing holds;
* an **expired** state, showing how an occurrence ends and how its validity
  window is reported.

They are built from the contract layer itself and written to
``fixtures/falcon/`` by :func:`write_golden_files`, so a golden file can never
drift from the envelope it claims to exemplify: a test rebuilds them and
compares byte for byte.

Each golden file is self-describing. ``canonical_json`` is the exact line
HELIOS publishes — byte-stable, sorted keys, no insignificant whitespace — and
``payload`` is the identical content indented so a human can read it.

These are illustrative *state*, not approved strategies. The numbers were
chosen to be legible and to sit in the same $2400 gold universe as
``fixtures/hermes/``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from helios.clock import from_iso8601_utc
from helios.contracts.output import (
    ENVELOPE_SCHEMA_VERSION,
    ComponentProvenance,
    EnvelopeKind,
    InputFreshness,
    StrategyStateEnvelope,
    Validity,
)
from helios.contracts.state import Direction, StrategyState
from helios.errors import ContractViolationError

#: The golden-document format itself (the wrapper, not the payload inside it).
GOLDEN_SCHEMA_VERSION = "helios.contract_golden/1.0.0"

#: Where the golden contract fixtures are checked in, relative to the repo root.
GOLDEN_DIR = Path("fixtures/falcon")

#: The HERMES fact schema version behind every exemplar's inputs.
_FACT_SCHEMA_VERSION = "hermes.market_fact/1.0.0"

_REQUIRED_GOLDEN_KEYS = (
    "golden_schema_version",
    "golden_id",
    "description",
    "expectations",
    "envelope_schema_version",
    "canonical_json",
    "payload",
)


def _utc(text: str):
    return from_iso8601_utc(text)


def _input(
    *,
    timeframe: str,
    role: str,
    frame_timestamp_utc: str,
    max_age_seconds: int,
    age_seconds: int = 60,
    is_fresh: bool = True,
    is_complete: bool = True,
) -> InputFreshness:
    return InputFreshness(
        instrument="XAU_USD",
        timeframe=timeframe,
        semantic_role=role,
        frame_timestamp_utc=_utc(frame_timestamp_utc),
        age_seconds=age_seconds,
        max_age_seconds=max_age_seconds,
        is_fresh=is_fresh,
        is_complete=is_complete,
        source="hermes",
        schema_version=_FACT_SCHEMA_VERSION,
    )


@dataclass(frozen=True, slots=True)
class GoldenExemplar:
    """One exemplar published state and what it is here to prove."""

    golden_id: str
    description: str
    expectations: tuple[str, ...]
    envelope: StrategyStateEnvelope

    @property
    def filename(self) -> str:
        return f"{self.golden_id}.json"

    @property
    def canonical_json(self) -> str:
        """The exact line HELIOS publishes for this state."""
        return self.envelope.to_canonical_json()

    def as_document(self) -> dict[str, Any]:
        """The self-describing golden document written to disk."""
        canonical = self.canonical_json
        return {
            "golden_schema_version": GOLDEN_SCHEMA_VERSION,
            "golden_id": self.golden_id,
            "description": self.description,
            "expectations": list(self.expectations),
            "envelope_schema_version": self.envelope.schema_version,
            "canonical_json": canonical,
            "payload": json.loads(canonical),
        }

    def render(self) -> str:
        """The exact text of the checked-in golden file."""
        return json.dumps(self.as_document(), indent=2, sort_keys=True) + "\n"


def _atomic_matched() -> GoldenExemplar:
    evaluated = _utc("2026-01-06T00:00:00Z")
    return GoldenExemplar(
        golden_id="atomic_matched",
        description=(
            "An atomic strategy whose condition became true on this evaluation. "
            "ema_50 crossed above ema_200 on the H4 CONTEXT frame."
        ),
        expectations=(
            "kind is ATOMIC, so chain_id, chain_version and components are empty",
            "an atomic envelope states the timeframe and semantic role it read",
            "strength is an exact decimal string, never a JSON number",
            "the match edge sets first_matched, last_matched and active_since "
            "to the same instant",
            "validity is bounded: this package declares a six-frame expiry",
        ),
        envelope=StrategyStateEnvelope(
            kind=EnvelopeKind.ATOMIC,
            strategy_id="golden_cross",
            strategy_version="1.0.0",
            instrument="XAU_USD",
            timeframe="H4",
            semantic_role="CONTEXT",
            state=StrategyState.MATCHED,
            direction=Direction.LONG,
            strength="0.75",
            evidence={
                "ema_50": "2402.40",
                "ema_200": "2400.00",
                "separation": "2.40",
                "min_separation": "0.25",
                "frames_since_cross": 0,
            },
            explanation=(
                "ema_50 (2402.40) crossed above ema_200 (2400.00) on the H4 "
                "CONTEXT frame opening 2026-01-05T20:00:00Z; separation 2.40 "
                "clears the declared minimum of 0.25."
            ),
            first_matched_at_utc=evaluated,
            last_matched_at_utc=evaluated,
            active_since_utc=evaluated,
            last_evaluated_at_utc=evaluated,
            validity=Validity(
                valid_from_utc=evaluated,
                valid_until_utc=_utc("2026-01-07T00:00:00Z"),
                reason="six H4 frames from the match, as the package declares",
            ),
            inputs=(
                _input(
                    timeframe="H4",
                    role="CONTEXT",
                    frame_timestamp_utc="2026-01-05T20:00:00Z",
                    max_age_seconds=21660,
                ),
            ),
        ),
    )


def _atomic_no_match() -> GoldenExemplar:
    evaluated = _utc("2026-01-06T00:05:00Z")
    return GoldenExemplar(
        golden_id="atomic_no_match",
        description=(
            "An atomic strategy that was evaluated and did not match. HELIOS "
            "publishes the non-match, with an explanation, rather than staying "
            "silent."
        ),
        expectations=(
            "a DORMANT state carries no match history at all",
            "a non-match still publishes an explanation and its evidence",
            "strength is null where the strategy declares none, and the key is "
            "still present",
            "direction NEUTRAL means a directional strategy currently finds no "
            "bias; it is not the same as NONE",
            "inputs are published even when nothing matched, so a consumer can "
            "tell 'evaluated and false' from 'not evaluated'",
        ),
        envelope=StrategyStateEnvelope(
            kind=EnvelopeKind.ATOMIC,
            strategy_id="range_breakout",
            strategy_version="1.0.0",
            instrument="XAU_USD",
            timeframe="M5",
            semantic_role="TRIGGER",
            state=StrategyState.DORMANT,
            direction=Direction.NEUTRAL,
            strength=None,
            evidence={
                "range_high": "2410.00",
                "range_low": "2395.50",
                "close": "2408.50",
                "lookback_bars": 20,
            },
            explanation=(
                "close 2408.50 stayed inside the 20-bar M5 range "
                "2395.50-2410.00; no declared precondition holds."
            ),
            first_matched_at_utc=None,
            last_matched_at_utc=None,
            active_since_utc=None,
            last_evaluated_at_utc=evaluated,
            validity=Validity(
                valid_from_utc=evaluated,
                valid_until_utc=None,
                reason="a non-match is open-ended: it holds until the next "
                "evaluation says otherwise",
            ),
            inputs=(
                _input(
                    timeframe="M5",
                    role="TRIGGER",
                    frame_timestamp_utc="2026-01-06T00:00:00Z",
                    max_age_seconds=510,
                ),
            ),
        ),
    )


def _chain_matched() -> GoldenExemplar:
    evaluated = _utc("2026-01-06T00:05:00Z")
    return GoldenExemplar(
        golden_id="chain_matched",
        description=(
            "A CONTEXT_TRIGGER chain that matched: H4 directional context "
            "agreeing with an M5 trigger, with full component provenance."
        ),
        expectations=(
            "kind is CHAIN, and chain_id/chain_version repeat "
            "strategy_id/strategy_version so identity reads uniformly",
            "components carry each contributing strategy's identity, version, "
            "state, direction, timeframe, semantic role and contribution",
            "a component's matched flag can never contradict its state",
            "the chain explains why it matched, as the PID requires",
            "the chain itself states no timeframe: its components do",
            "inputs cover every frame behind the decision, one per role",
        ),
        envelope=StrategyStateEnvelope(
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
            evidence={"components_satisfied": 2, "components_required": 2},
            explanation=(
                "CONTEXT golden_cross@1.0.0 is ACTIVE LONG on H4 and TRIGGER "
                "range_breakout@1.0.0 MATCHED LONG on M5; both directions agree "
                "as the chain requires, so the chain matched."
            ),
            first_matched_at_utc=evaluated,
            last_matched_at_utc=evaluated,
            active_since_utc=evaluated,
            last_evaluated_at_utc=evaluated,
            validity=Validity(
                valid_from_utc=evaluated,
                valid_until_utc=_utc("2026-01-06T04:05:00Z"),
                reason="14400 seconds from the match, as the package declares",
            ),
            components=(
                ComponentProvenance(
                    strategy_id="golden_cross",
                    strategy_version="1.0.0",
                    state=StrategyState.ACTIVE,
                    direction=Direction.LONG,
                    timeframe="H4",
                    semantic_role="CONTEXT",
                    sequence_index=None,
                    matched=True,
                    last_evaluated_at_utc=evaluated,
                    contribution="directional context, matched at "
                    "2026-01-06T00:00:00Z and still holding",
                ),
                ComponentProvenance(
                    strategy_id="range_breakout",
                    strategy_version="1.0.0",
                    state=StrategyState.MATCHED,
                    direction=Direction.LONG,
                    timeframe="M5",
                    semantic_role="TRIGGER",
                    sequence_index=None,
                    matched=True,
                    last_evaluated_at_utc=evaluated,
                    contribution="close 2413.50 cleared the 20-bar range high "
                    "2410.00",
                ),
            ),
            inputs=(
                _input(
                    timeframe="H4",
                    role="CONTEXT",
                    frame_timestamp_utc="2026-01-05T20:00:00Z",
                    max_age_seconds=21660,
                    age_seconds=300,
                ),
                _input(
                    timeframe="M5",
                    role="TRIGGER",
                    frame_timestamp_utc="2026-01-06T00:00:00Z",
                    max_age_seconds=510,
                ),
            ),
        ),
    )


def _chain_expired() -> GoldenExemplar:
    matched = _utc("2026-01-06T00:05:00Z")
    evaluated = _utc("2026-01-06T04:10:00Z")
    return GoldenExemplar(
        golden_id="chain_expired",
        description=(
            "The same chain, later: the match aged past its declared validity "
            "window without being invalidated, so the occurrence EXPIRED."
        ),
        expectations=(
            "EXPIRED and INVALID are distinct: nothing broke here, time ran out",
            "an expired state retains its match history so a consumer can see "
            "what ended and when",
            "active_since is cleared once the state is no longer live",
            "validity carries valid_until_utc and a reason in the strategy's "
            "own terms",
            "component provenance survives the end of the occurrence, "
            "including a component that has since fallen dormant",
            "an expired input may be published as not fresh; the freshness "
            "verdict is reported, not hidden",
        ),
        envelope=StrategyStateEnvelope(
            kind=EnvelopeKind.CHAIN,
            strategy_id="gold_context_trigger",
            strategy_version="1.0.0",
            chain_id="gold_context_trigger",
            chain_version="1.0.0",
            instrument="XAU_USD",
            timeframe=None,
            semantic_role=None,
            state=StrategyState.EXPIRED,
            direction=Direction.LONG,
            strength=None,
            evidence={"components_satisfied": 1, "components_required": 2},
            explanation=(
                "the chain match reached its declared expiry at "
                "2026-01-06T04:05:00Z. TRIGGER range_breakout@1.0.0 returned to "
                "DORMANT while CONTEXT golden_cross@1.0.0 stayed ACTIVE, so the "
                "occurrence aged out rather than being invalidated."
            ),
            first_matched_at_utc=matched,
            last_matched_at_utc=matched,
            active_since_utc=None,
            last_evaluated_at_utc=evaluated,
            validity=Validity(
                valid_from_utc=matched,
                valid_until_utc=_utc("2026-01-06T04:05:00Z"),
                reason="14400 seconds elapsed since the match without an "
                "invalidation condition firing",
            ),
            components=(
                ComponentProvenance(
                    strategy_id="golden_cross",
                    strategy_version="1.0.0",
                    state=StrategyState.ACTIVE,
                    direction=Direction.LONG,
                    timeframe="H4",
                    semantic_role="CONTEXT",
                    sequence_index=None,
                    matched=True,
                    last_evaluated_at_utc=evaluated,
                    contribution="context still holds; it did not cause the "
                    "expiry",
                ),
                ComponentProvenance(
                    strategy_id="range_breakout",
                    strategy_version="1.0.0",
                    state=StrategyState.DORMANT,
                    direction=Direction.NEUTRAL,
                    timeframe="M5",
                    semantic_role="TRIGGER",
                    sequence_index=None,
                    matched=False,
                    last_evaluated_at_utc=evaluated,
                    contribution="close fell back inside the range; the "
                    "trigger no longer holds",
                ),
            ),
            inputs=(
                _input(
                    timeframe="H4",
                    role="CONTEXT",
                    frame_timestamp_utc="2026-01-06T00:00:00Z",
                    max_age_seconds=21660,
                    age_seconds=600,
                ),
                _input(
                    timeframe="M5",
                    role="TRIGGER",
                    frame_timestamp_utc="2026-01-06T04:05:00Z",
                    max_age_seconds=510,
                    age_seconds=300,
                ),
            ),
        ),
    )


def golden_exemplars() -> tuple[GoldenExemplar, ...]:
    """Every exemplar published state, in a stable order."""
    return (
        _atomic_matched(),
        _atomic_no_match(),
        _chain_matched(),
        _chain_expired(),
    )


def read_golden_document(path: Path | str) -> dict[str, Any]:
    """Read a golden contract fixture, failing loudly on anything unexpected."""
    path = Path(path)
    if not path.is_file():
        raise ContractViolationError("golden contract fixture not found", origin=str(path))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractViolationError(
            "malformed JSON in golden contract fixture", origin=str(path), detail=str(exc)
        ) from exc
    if not isinstance(document, Mapping):
        raise ContractViolationError(
            "a golden contract fixture must be a mapping", origin=str(path)
        )
    missing = [key for key in _REQUIRED_GOLDEN_KEYS if key not in document]
    if missing:
        raise ContractViolationError(
            "golden contract fixture is missing required keys",
            origin=str(path),
            missing=sorted(missing),
        )
    declared = document["golden_schema_version"]
    if declared != GOLDEN_SCHEMA_VERSION:
        raise ContractViolationError(
            "unknown golden_schema_version",
            origin=str(path),
            received=declared,
            supported=GOLDEN_SCHEMA_VERSION,
        )
    if document["envelope_schema_version"] != ENVELOPE_SCHEMA_VERSION:
        raise ContractViolationError(
            "golden contract fixture exemplifies an envelope version this build "
            "does not publish",
            origin=str(path),
            received=document["envelope_schema_version"],
            supported=ENVELOPE_SCHEMA_VERSION,
        )
    return dict(document)


def write_golden_files(root: Path | str = ".") -> tuple[Path, ...]:
    """(Re)generate every checked-in golden contract fixture."""
    directory = Path(root) / GOLDEN_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for exemplar in golden_exemplars():
        target = directory / exemplar.filename
        target.write_text(exemplar.render(), encoding="utf-8")
        written.append(target)
    return tuple(written)


if __name__ == "__main__":  # pragma: no cover - operator utility
    for path in write_golden_files():
        print(path)
