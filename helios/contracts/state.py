"""Strategy state model and direction semantics.

The PID names seven state concepts. HELIOS v1 adopts all seven **verbatim** —
``DORMANT, FORMING, MATCHED, ACTIVE, WEAKENING, INVALID, EXPIRED``. The PID
permits FORGE to refine the naming; no refinement was needed, and keeping the
PID's own vocabulary keeps HSA specifications, FALCON consumers and this
engine speaking one language. What the PID did *not* fix — the legal
transitions between those states — is defined explicitly below and tested.

Nothing here knows about trade lifecycle. These states describe whether a
*strategy condition* holds, and for how long. Downstream systems may act on a
state any number of times, or never; HELIOS state is unchanged either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic_core import core_schema

from helios.clock import ensure_utc
from helios.errors import IllegalStateTransitionError


class _CodedEnum(Enum):
    """Enum whose members validate/serialise as their plain string code."""

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def parse(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            candidate = value.strip().upper()
            for member in cls:
                if member.value == candidate:
                    return member
        raise ValueError(
            f"unsupported {cls.__name__}: {value!r}; "
            f"expected one of {[member.value for member in cls]}"
        )

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        return core_schema.no_info_plain_validator_function(
            cls.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda member: member.value,
                return_schema=core_schema.str_schema(),
                when_used="always",
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> Any:
        return {
            "type": "string",
            "enum": [member.value for member in cls],
            "title": cls.__name__,
        }


class Direction(_CodedEnum):
    """Directional bias a strategy expresses.

    ``NEUTRAL`` and ``NONE`` are deliberately distinct:

    * ``NEUTRAL`` — the strategy *is* direction-aware and currently finds no
      directional bias (e.g. price sits exactly between its references).
    * ``NONE`` — the strategy is not directional at all (e.g. a pure
      volatility-regime condition). Direction is not applicable, not absent.

    Collapsing the two would make a non-directional strategy indistinguishable
    from a directional one that has nothing to say, which chains need to tell
    apart when relating component directions.
    """

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    NONE = "NONE"

    @property
    def is_directional(self) -> bool:
        return self in (Direction.LONG, Direction.SHORT)

    @property
    def opposite(self) -> "Direction":
        if self is Direction.LONG:
            return Direction.SHORT
        if self is Direction.SHORT:
            return Direction.LONG
        return self

    def agrees_with(self, other: "Direction") -> bool:
        """True when both are the same directional bias (LONG/LONG, SHORT/SHORT)."""
        return self.is_directional and self is other

    def opposes(self, other: "Direction") -> bool:
        """True when the two are opposing directional biases."""
        return self.is_directional and other.is_directional and self is not other


class StrategyState(_CodedEnum):
    """Deterministic strategy/chain state.

    * ``DORMANT`` — evaluated; no part of the condition currently holds.
    * ``FORMING`` — some declared precondition holds, the full condition does
      not yet. A partially satisfied chain sits here.
    * ``MATCHED`` — the full condition became true on this evaluation. This is
      the transition edge, published exactly at the moment of match.
    * ``ACTIVE`` — the match has been observed before and still holds.
    * ``WEAKENING`` — the match still holds but a declared strength/quality
      measure is degrading. Advisory, still live.
    * ``INVALID`` — a declared invalidation condition fired; the match is void.
    * ``EXPIRED`` — the match aged past its declared validity window without
      being invalidated. Distinct from ``INVALID``: nothing broke, time ran out.
    """

    DORMANT = "DORMANT"
    FORMING = "FORMING"
    MATCHED = "MATCHED"
    ACTIVE = "ACTIVE"
    WEAKENING = "WEAKENING"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


#: States in which a strategy's declared condition is currently satisfied.
LIVE_STATES: frozenset[StrategyState] = frozenset(
    {StrategyState.MATCHED, StrategyState.ACTIVE, StrategyState.WEAKENING}
)

#: States in which this occurrence of the condition is over. A strategy leaves
#: a resolved state only by returning to DORMANT and starting a fresh cycle.
RESOLVED_STATES: frozenset[StrategyState] = frozenset(
    {StrategyState.INVALID, StrategyState.EXPIRED}
)


def _transitions() -> Mapping[StrategyState, frozenset[StrategyState]]:
    S = StrategyState
    table: dict[StrategyState, frozenset[StrategyState]] = {
        # Nothing holds. Conditions may start forming, or match outright — an
        # atomic such as a moving-average cross matches with no forming phase.
        # INVALID is reachable too: a strategy that has never matched but whose
        # evaluation raised is exactly as invalid as one whose match broke, and
        # republishing DORMANT would falsely assert "we evaluated and nothing
        # holds" when in fact nothing was evaluated.
        S.DORMANT: frozenset({S.DORMANT, S.FORMING, S.MATCHED, S.INVALID}),
        # Partially satisfied. It may complete, fade back to dormant, be hard
        # invalidated, or run out of its forming window.
        S.FORMING: frozenset({S.FORMING, S.MATCHED, S.DORMANT, S.INVALID, S.EXPIRED}),
        # The match edge. It persists (ACTIVE), degrades, breaks or ages out.
        # It may NOT fall back to DORMANT/FORMING: once matched, this
        # occurrence must resolve explicitly so the lifecycle stays auditable.
        S.MATCHED: frozenset({S.MATCHED, S.ACTIVE, S.WEAKENING, S.INVALID, S.EXPIRED}),
        S.ACTIVE: frozenset({S.ACTIVE, S.WEAKENING, S.INVALID, S.EXPIRED}),
        # Weakening may recover to ACTIVE; it never re-fires the MATCHED edge,
        # because the match already happened.
        S.WEAKENING: frozenset({S.WEAKENING, S.ACTIVE, S.INVALID, S.EXPIRED}),
        # Resolved states rearm only via DORMANT — DORMANT is the only
        # NON-resolved successor either of them has. EXPIRED may still become
        # INVALID, because "void" is not a rearm: an occurrence that aged out
        # and whose next evaluation cannot assert anything at all is invalid,
        # and republishing EXPIRED would keep asserting "time ran out" about an
        # evaluation that never happened.
        S.INVALID: frozenset({S.INVALID, S.DORMANT}),
        S.EXPIRED: frozenset({S.EXPIRED, S.DORMANT, S.INVALID}),
    }
    return MappingProxyType({key: value for key, value in table.items()})


#: The complete legal transition table. Self-transitions are legal everywhere:
#: HELIOS re-evaluates continuously and republishes an unchanged state.
LEGAL_TRANSITIONS: Mapping[StrategyState, frozenset[StrategyState]] = _transitions()


def legal_successors(state: StrategyState) -> frozenset[StrategyState]:
    """States reachable from ``state`` in one evaluation."""
    return LEGAL_TRANSITIONS[state]


def is_legal_transition(current: StrategyState, proposed: StrategyState) -> bool:
    return proposed in LEGAL_TRANSITIONS[current]


@dataclass(frozen=True, slots=True)
class StateTransition:
    """An immutable record of one state change and why it happened."""

    previous: StrategyState
    current: StrategyState
    at_utc: datetime
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "at_utc", ensure_utc(self.at_utc, field="at_utc"))
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise IllegalStateTransitionError(
                "a state transition must carry a non-empty reason",
                previous=str(self.previous),
                current=str(self.current),
            )

    @property
    def changed(self) -> bool:
        return self.previous is not self.current


def transition(
    current: StrategyState,
    proposed: StrategyState,
    *,
    at_utc: datetime,
    reason: str,
) -> StateTransition:
    """Validate and record a transition, failing loudly if it is not legal."""
    if not isinstance(current, StrategyState) or not isinstance(proposed, StrategyState):
        raise IllegalStateTransitionError(
            "transition requires StrategyState values",
            current=repr(current),
            proposed=repr(proposed),
        )
    if not is_legal_transition(current, proposed):
        raise IllegalStateTransitionError(
            "illegal strategy-state transition",
            previous=str(current),
            proposed=str(proposed),
            legal=sorted(member.value for member in legal_successors(current)),
        )
    return StateTransition(previous=current, current=proposed, at_utc=at_utc, reason=reason)
