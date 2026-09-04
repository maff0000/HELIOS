"""What each component contributed to a chain, and why.

The PID requires an explicit explanation of why a chain matched **or did not
match**. That is only possible if a non-match is a structured result rather
than an absent one, so every evaluation produces one
:class:`ComponentOutcome` per declared component — including for a component
whose state was never supplied. The chain's prose explanation is rendered from
these outcomes, never accumulated ad hoc, so the published explanation and the
published provenance can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from helios.clock import to_iso8601_utc
from helios.contracts._tokens import SemanticRole
from helios.contracts.identity import StrategyIdentity
from helios.contracts.output import ComponentProvenance, StrategyStateEnvelope
from helios.contracts.state import LIVE_STATES, Direction, StrategyState, _CodedEnum
from helios.contracts.timeframe import Timeframe
from helios.spec.model import DirectionRelationship


class ComponentReason(_CodedEnum):
    """Exactly why one component did or did not count toward the chain.

    Every non-satisfying reason is a *rule*, not an accident: none of them can
    be reached by a crash, a silent pass or a default.
    """

    #: The component holds and counts toward the chain.
    SATISFIED = "SATISFIED"
    #: No state was supplied for this component in this evaluation.
    NOT_SUPPLIED = "NOT_SUPPLIED"
    #: A state was supplied, but for a different version than the chain declares.
    VERSION_MISMATCH = "VERSION_MISMATCH"
    #: At least one market fact behind the component's state was stale.
    STALE_COMPONENT_INPUT = "STALE_COMPONENT_INPUT"
    #: The component's own declared validity window had already passed.
    COMPONENT_VALIDITY_LAPSED = "COMPONENT_VALIDITY_LAPSED"
    #: The component declared its own match void.
    COMPONENT_INVALIDATED = "COMPONENT_INVALIDATED"
    #: The component's match aged out before this evaluation.
    COMPONENT_AGED_OUT = "COMPONENT_AGED_OUT"
    #: The component's state is not one the chain declared as satisfying.
    STATE_NOT_REQUIRED = "STATE_NOT_REQUIRED"
    #: The component's direction does not stand in the declared relationship.
    DIRECTION_INCOMPATIBLE = "DIRECTION_INCOMPATIBLE"
    #: An ordered chain needs a match instant and the component published none.
    NO_MATCH_INSTANT = "NO_MATCH_INSTANT"
    #: The component matched earlier than a component declared before it.
    OUT_OF_DECLARED_ORDER = "OUT_OF_DECLARED_ORDER"
    #: The context had not been established when the trigger matched.
    CONTEXT_NOT_ESTABLISHED_FIRST = "CONTEXT_NOT_ESTABLISHED_FIRST"
    #: The context's own validity had lapsed by the time the trigger matched.
    CONTEXT_VALIDITY_LAPSED = "CONTEXT_VALIDITY_LAPSED"


@dataclass(frozen=True, slots=True)
class ComponentOutcome:
    """One declared component's contribution to one chain evaluation."""

    declared: StrategyIdentity
    role: Optional[SemanticRole]
    timeframe: Optional[Timeframe]
    sequence_index: Optional[int]
    relationship: DirectionRelationship
    required_states: tuple[StrategyState, ...]
    envelope: Optional[StrategyStateEnvelope]
    satisfied: bool
    reason: ComponentReason
    detail: str

    @property
    def observed_state(self) -> Optional[StrategyState]:
        return self.envelope.state if self.envelope is not None else None

    @property
    def observed_direction(self) -> Optional[Direction]:
        return self.envelope.direction if self.envelope is not None else None

    @property
    def matched_at_utc(self) -> Optional[datetime]:
        """When this occurrence of the component's condition first matched.

        Ordering uses ``first_matched_at_utc`` rather than
        ``last_matched_at_utc`` because the latter advances on every live
        evaluation: a component that matched first but is still ACTIVE would
        otherwise appear to have matched last.
        """
        return self.envelope.first_matched_at_utc if self.envelope is not None else None

    def failing(self, reason: ComponentReason, detail: str) -> "ComponentOutcome":
        """A copy marked unsatisfied for a further rule. Outcomes are immutable."""
        return ComponentOutcome(
            declared=self.declared,
            role=self.role,
            timeframe=self.timeframe,
            sequence_index=self.sequence_index,
            relationship=self.relationship,
            required_states=self.required_states,
            envelope=self.envelope,
            satisfied=False,
            reason=reason,
            detail=detail,
        )

    @property
    def label(self) -> str:
        """``golden_cross@1.0.0 (CONTEXT, H4, index 0)`` — stable, sortable prose."""
        parts = []
        if self.role is not None:
            parts.append(str(self.role))
        if self.timeframe is not None:
            parts.append(self.timeframe.code)
        if self.sequence_index is not None:
            parts.append(f"index {self.sequence_index}")
        if not parts:
            return self.declared.canonical
        return f"{self.declared.canonical} ({', '.join(parts)})"

    def describe(self) -> str:
        """One sentence naming this component's contribution or its failure."""
        if self.satisfied:
            envelope = self.envelope
            assert envelope is not None  # SATISFIED implies a supplied envelope
            when = (
                to_iso8601_utc(envelope.first_matched_at_utc)
                if envelope.first_matched_at_utc is not None
                else "no match instant"
            )
            return (
                f"{self.label} {envelope.state} {envelope.direction} at {when}"
            )
        return f"{self.label} is not satisfied — {self.reason}: {self.detail}"

    def provenance(self, *, evaluated_at_utc: datetime) -> ComponentProvenance:
        """The publishable per-component provenance record.

        A component whose state was not supplied, or was supplied for a
        version the chain does not declare, is published as ``INVALID``: the
        output contract has no ``UNKNOWN`` state, and publishing ``DORMANT``
        would claim HELIOS observed the component saying so. ``contribution``
        always names the exact rule that applied.

        ``matched`` is the contract's own meaning — whether the component's
        condition holds — and the contract validates it against ``state``.
        Whether the component satisfied *this chain* is a different question,
        answered by ``contribution``.
        """
        envelope = self.envelope
        if envelope is None:
            return ComponentProvenance(
                strategy_id=self.declared.strategy_id,
                strategy_version=self.declared.strategy_version,
                state=StrategyState.INVALID,
                direction=Direction.NONE,
                timeframe=self.timeframe,
                semantic_role=self.role,
                sequence_index=self.sequence_index,
                matched=False,
                last_evaluated_at_utc=evaluated_at_utc,
                contribution=f"{self.reason}: {self.detail}",
            )
        return ComponentProvenance(
            strategy_id=envelope.strategy_id,
            strategy_version=envelope.strategy_version,
            state=envelope.state,
            direction=envelope.direction,
            timeframe=self.timeframe or envelope.timeframe,
            semantic_role=self.role or envelope.semantic_role,
            sequence_index=self.sequence_index,
            matched=envelope.state in LIVE_STATES,
            last_evaluated_at_utc=envelope.last_evaluated_at_utc,
            contribution=f"{self.reason}: {self.detail}",
        )


@dataclass(frozen=True, slots=True)
class ChainAssessment:
    """The composed verdict for one evaluation, before state transition.

    Separating "does the composed condition hold right now" from "what state
    does the chain therefore publish" keeps the temporal machinery (lifecycle,
    persistence, expiry) in one place and makes both halves testable alone.
    """

    satisfied: bool
    direction: Direction
    outcomes: tuple[ComponentOutcome, ...]
    chain_reasons: tuple[str, ...]

    @property
    def satisfied_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.satisfied)

    @property
    def declared_count(self) -> int:
        return len(self.outcomes)

    @property
    def unsatisfied(self) -> tuple[ComponentOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.satisfied)

    @property
    def first_unsatisfied(self) -> Optional[ComponentOutcome]:
        """The first failure in canonical component order — a stable culprit."""
        unsatisfied = self.unsatisfied
        return unsatisfied[0] if unsatisfied else None
