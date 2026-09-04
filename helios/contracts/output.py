"""The normalised output contract.

ONE envelope is published for atomic strategies and for chains alike. FALCON
reads ``strategy_id``, ``strategy_version``, ``state`` and ``direction`` the
same way regardless of what produced them, and never reverse-engineers a
strategy-specific format.

Design decision — chains and the uniform read
---------------------------------------------
For ``kind = CHAIN`` the chain's identity is published in BOTH
``strategy_id``/``strategy_version`` (the identity of the publishing unit,
which every consumer can read without branching) and
``chain_id``/``chain_version`` (which the PID requires by name, and which
chain-aware consumers key on). The two are validated to agree, so the
duplication cannot drift. ``kind`` disambiguates, and ``components`` carries
per-component provenance for composites.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Optional, Union

from pydantic import AfterValidator, BeforeValidator, field_validator, model_validator

from helios.contracts._fields import (
    DecimalValue,
    FrozenMapping,
    HeliosModel,
    UtcDatetime,
    freeze_mapping,
    validate_model,
)
from helios.contracts._tokens import Instrument, SemanticRole, SourceName
from helios.contracts.freshness import FreshnessVerdict
from helios.contracts.identity import (
    ChainId,
    StrategyId,
    StrategyVersion,
)
from helios.contracts.lifecycle import LifecycleTimestamps
from helios.contracts.serialisation import canonical_dumps, canonical_loads
from helios.contracts.state import (
    LIVE_STATES,
    Direction,
    StrategyState,
    _CodedEnum,
)
from helios.contracts.timeframe import Timeframe
from helios.errors import ContractViolationError

#: Version of the published envelope itself. A consumer that does not
#: recognise this value must refuse the payload rather than guess.
ENVELOPE_SCHEMA_VERSION = "helios.strategy_state/1.0.0"


class EnvelopeKind(_CodedEnum):
    """Whether the publishing unit is a single strategy or a composition."""

    ATOMIC = "ATOMIC"
    CHAIN = "CHAIN"


def _coerce_evidence_value(value: Any) -> Any:
    """Evidence entries are flat, exactly-representable scalars only.

    Evidence explains *this* evaluation. It is deliberately not a nested
    document store: CER owns durable empirical evidence, and HELIOS must not
    grow a competing one.
    """
    if value is None or isinstance(value, (bool, int, str, Decimal)):
        return value
    if isinstance(value, float):
        raise ContractViolationError(
            "binary float rejected in evidence: supply a decimal string or Decimal",
            value=repr(value),
        )
    raise ContractViolationError(
        "evidence values must be scalars (string, integer, decimal, boolean or null)",
        received_type=type(value).__name__,
    )


EvidenceValue = Annotated[
    Union[bool, int, Decimal, str, None], BeforeValidator(_coerce_evidence_value)
]

#: Strategy-declared evidence: a flat, immutable mapping of scalars.
Evidence = Annotated[dict[str, EvidenceValue], AfterValidator(freeze_mapping)]


class Validity(HeliosModel):
    """Temporal validity of the published state.

    ``valid_until_utc`` is ``None`` for an open-ended state — that is an
    explicit "no declared expiry", not a missing value. ``reason`` explains an
    ``INVALID``/``EXPIRED`` state in the strategy's own terms.
    """

    valid_from_utc: Optional[UtcDatetime] = None
    valid_until_utc: Optional[UtcDatetime] = None
    reason: Optional[str] = None

    @model_validator(mode="after")
    def _window_is_ordered(self) -> "Validity":
        if (
            self.valid_from_utc is not None
            and self.valid_until_utc is not None
            and self.valid_until_utc < self.valid_from_utc
        ):
            raise ContractViolationError(
                "validity window ends before it begins",
                valid_from_utc=self.valid_from_utc.isoformat(),
                valid_until_utc=self.valid_until_utc.isoformat(),
            )
        return self


class InputFreshness(HeliosModel):
    """Freshness and source metadata for one market-fact input behind a state."""

    instrument: Instrument
    timeframe: Timeframe
    semantic_role: Optional[SemanticRole] = None
    frame_timestamp_utc: UtcDatetime
    age_seconds: int
    max_age_seconds: int
    is_fresh: bool
    is_complete: bool
    source: SourceName
    schema_version: str

    @classmethod
    def from_verdict(
        cls, verdict: FreshnessVerdict, *, semantic_role: Optional[SemanticRole] = None
    ) -> "InputFreshness":
        return cls(
            instrument=verdict.instrument,
            timeframe=verdict.timeframe,
            semantic_role=semantic_role,
            frame_timestamp_utc=verdict.frame_timestamp_utc,
            age_seconds=verdict.age_seconds,
            max_age_seconds=verdict.max_age_seconds,
            is_fresh=verdict.is_fresh,
            is_complete=verdict.is_complete,
            source=verdict.source,
            schema_version=verdict.schema_version,
        )


class ComponentProvenance(HeliosModel):
    """What one component contributed to a chain's state.

    v1 chains consume atomic strategies directly, so a component references an
    atomic identity. Chain-of-chain recursion is deliberately not modelled;
    the PID requires explicit architecture authority before introducing it.
    """

    strategy_id: StrategyId
    strategy_version: StrategyVersion
    state: StrategyState
    direction: Direction
    timeframe: Optional[Timeframe] = None
    semantic_role: Optional[SemanticRole] = None
    sequence_index: Optional[int] = None
    matched: bool
    last_evaluated_at_utc: UtcDatetime
    contribution: Optional[str] = None

    @field_validator("sequence_index")
    @classmethod
    def _sequence_index_is_not_negative(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and (isinstance(value, bool) or value < 0):
            raise ContractViolationError("sequence_index must be a non-negative integer",
                                         value=repr(value))
        return value

    @model_validator(mode="after")
    def _matched_agrees_with_state(self) -> "ComponentProvenance":
        if self.matched != (self.state in LIVE_STATES):
            raise ContractViolationError(
                "component 'matched' flag contradicts its state",
                state=self.state.value,
                matched=self.matched,
            )
        return self


class StrategyStateEnvelope(HeliosModel):
    """The single normalised output contract for atomic strategies and chains."""

    schema_version: str = ENVELOPE_SCHEMA_VERSION
    kind: EnvelopeKind

    # Identity — CER-compatible. HELIOS originates these two; CER owns
    # experiment_id / run_id / evidence_id / artifact_id and HELIOS publishes
    # none of them.
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    chain_id: Optional[ChainId] = None
    chain_version: Optional[StrategyVersion] = None

    # Subject
    instrument: Instrument
    timeframe: Optional[Timeframe] = None
    semantic_role: Optional[SemanticRole] = None

    # State
    state: StrategyState
    direction: Direction
    strength: Optional[DecimalValue] = None
    evidence: Evidence = {}
    explanation: Optional[str] = None

    # Lifecycle instants (UTC)
    first_matched_at_utc: Optional[UtcDatetime] = None
    last_matched_at_utc: Optional[UtcDatetime] = None
    active_since_utc: Optional[UtcDatetime] = None
    last_evaluated_at_utc: UtcDatetime

    # Validity / expiry
    validity: Validity

    # Composition provenance
    components: tuple[ComponentProvenance, ...] = ()

    # Freshness / source metadata for the inputs behind this state
    inputs: tuple[InputFreshness, ...]

    @field_validator("schema_version")
    @classmethod
    def _schema_version_is_known(cls, value: str) -> str:
        if value != ENVELOPE_SCHEMA_VERSION:
            raise ContractViolationError(
                "unknown envelope schema_version; refusing to guess its meaning",
                received=value,
                supported=ENVELOPE_SCHEMA_VERSION,
            )
        return value

    @field_validator("strength")
    @classmethod
    def _strength_is_a_unit_interval(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and not (Decimal(0) <= value <= Decimal(1)):
            raise ContractViolationError(
                "strength must lie within 0..1 inclusive", value=str(value)
            )
        return value

    @model_validator(mode="after")
    def _kind_is_coherent(self) -> "StrategyStateEnvelope":
        if self.kind is EnvelopeKind.ATOMIC:
            if self.chain_id is not None or self.chain_version is not None:
                raise ContractViolationError(
                    "an ATOMIC envelope must not carry chain identity",
                    strategy_id=str(self.strategy_id),
                )
            if self.components:
                raise ContractViolationError(
                    "an ATOMIC envelope must not carry component provenance",
                    strategy_id=str(self.strategy_id),
                )
            if self.timeframe is None and self.semantic_role is None:
                raise ContractViolationError(
                    "an ATOMIC envelope must state a timeframe and/or a semantic role",
                    strategy_id=str(self.strategy_id),
                )
        else:
            if self.chain_id is None or self.chain_version is None:
                raise ContractViolationError(
                    "a CHAIN envelope must carry chain_id and chain_version",
                    strategy_id=str(self.strategy_id),
                )
            if self.chain_id.value != self.strategy_id.value:
                raise ContractViolationError(
                    "chain_id must equal strategy_id so consumers can read identity "
                    "uniformly across atomic and chain envelopes",
                    chain_id=str(self.chain_id),
                    strategy_id=str(self.strategy_id),
                )
            if self.chain_version != self.strategy_version:
                raise ContractViolationError(
                    "chain_version must equal strategy_version",
                    chain_version=str(self.chain_version),
                    strategy_version=str(self.strategy_version),
                )
            if not self.components:
                raise ContractViolationError(
                    "a CHAIN envelope must carry component provenance",
                    chain_id=str(self.chain_id),
                )
        return self

    @model_validator(mode="after")
    def _lifecycle_is_coherent(self) -> "StrategyStateEnvelope":
        if self.state in LIVE_STATES:
            if self.last_matched_at_utc is None or self.first_matched_at_utc is None:
                raise ContractViolationError(
                    "a live state must publish first_matched_at_utc and last_matched_at_utc",
                    state=self.state.value,
                )
            if self.active_since_utc is None:
                raise ContractViolationError(
                    "a live state must publish active_since_utc", state=self.state.value
                )
        if self.state is StrategyState.DORMANT and any(
            value is not None
            for value in (
                self.first_matched_at_utc,
                self.last_matched_at_utc,
                self.active_since_utc,
            )
        ):
            raise ContractViolationError(
                "a DORMANT state must not carry match history from a resolved occurrence"
            )
        for name in ("first_matched_at_utc", "last_matched_at_utc", "active_since_utc"):
            value: Optional[datetime] = getattr(self, name)
            if value is not None and value > self.last_evaluated_at_utc:
                raise ContractViolationError(
                    "lifecycle instant is later than last_evaluated_at_utc",
                    field=name,
                    value=value.isoformat(),
                    last_evaluated_at_utc=self.last_evaluated_at_utc.isoformat(),
                )
        if (
            self.first_matched_at_utc is not None
            and self.last_matched_at_utc is not None
            and self.last_matched_at_utc < self.first_matched_at_utc
        ):
            raise ContractViolationError("last_matched_at_utc precedes first_matched_at_utc")
        return self

    @classmethod
    def with_lifecycle(
        cls, lifecycle: LifecycleTimestamps, **fields: Any
    ) -> "StrategyStateEnvelope":
        """Construct an envelope from derived :class:`LifecycleTimestamps`."""
        return cls(
            first_matched_at_utc=lifecycle.first_matched_at_utc,
            last_matched_at_utc=lifecycle.last_matched_at_utc,
            active_since_utc=lifecycle.active_since_utc,
            last_evaluated_at_utc=lifecycle.last_evaluated_at_utc,
            **fields,
        )

    @property
    def is_live(self) -> bool:
        return self.state in LIVE_STATES

    def to_canonical_dict(self) -> dict[str, Any]:
        from helios.contracts.serialisation import canonicalise

        return canonicalise(self)

    def to_canonical_json(self) -> str:
        """Deterministic JSON bytes-stable representation for FALCON."""
        return canonical_dumps(self)

    @classmethod
    def from_canonical_json(cls, text: str) -> "StrategyStateEnvelope":
        """Parse a published envelope back, failing loudly on anything unknown."""
        return validate_model(cls, canonical_loads(text), label="StrategyStateEnvelope")


#: The exact field set FALCON can rely on. Used by the contract completeness
#: test and published in docs/CONTRACTS.md.
ENVELOPE_FIELDS: tuple[str, ...] = tuple(StrategyStateEnvelope.model_fields)
