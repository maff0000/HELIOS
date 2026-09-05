"""Shared scaffolding every atomic strategy sits on.

An atom's own module contains only its condition: read the declared facts,
decide whether the condition holds, in which direction, how strongly, and say
why. Everything an atom must *not* re-implement lives here, because getting it
subtly different per strategy is exactly how a fleet of strategies stops being
comparable:

* resolving the semantic role and timeframe the *package* bound (never a
  constant in atom source);
* refusing missing, stale or incomplete facts through the configured
  :class:`~helios.contracts.freshness.FreshnessPolicy`;
* turning a verdict into a state that respects the documented legal transition
  table, including persistence, weakening and expiry;
* deriving the lifecycle instants and publishing the normalised envelope.

Nothing here computes a market indicator. HERMES owns indicators; the only
arithmetic in this module is on facts a strategy was handed, and the ratio
helpers exist so that every derived quantity an atom publishes is quantised
the same way and is therefore byte-identical across runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Optional

from helios.contracts._fields import to_decimal
from helios.contracts._tokens import SemanticRole
from helios.contracts.freshness import FreshnessPolicy, FreshnessVerdict, require_fresh_frame
from helios.contracts.identity import StrategyIdentity
from helios.contracts.lifecycle import LifecycleTimestamps, advance_lifecycle
from helios.contracts.market_fact import MarketFactFrame
from helios.contracts.output import (
    EnvelopeKind,
    InputFreshness,
    StrategyStateEnvelope,
    Validity,
)
from helios.contracts.state import (
    LIVE_STATES,
    RESOLVED_STATES,
    Direction,
    StrategyState,
    transition,
)
from helios.contracts.timeframe import Timeframe
from helios.contracts.window import MarketFactWindow
from helios.determinism import deterministic_arithmetic
from helios.errors import ContractViolationError, StrategySpecError
from helios.protocols import EvaluationContext, RequiredInput
from helios.spec.model import EvaluateOn, ExpiryMode, ParameterType, StrategyPackage

#: Evidence key under which the run of consecutive evaluations satisfying the
#: condition is published. The envelope IS the strategy's state: an evaluation
#: is a pure fold of (previous envelope, facts) -> next envelope, so anything
#: an occurrence must remember has to be published rather than hidden in an
#: object's attributes. Publishing it also makes persistence auditable.
HOLD_RUN_KEY = "consecutive_hold_frames"

#: Precision every derived ratio is quantised to before publication. Two
#: processes computing the same ratio must produce the same digits, so the
#: precision is fixed here rather than inherited from an ambient decimal
#: context that a caller could have changed. The context the arithmetic runs
#: in is :data:`helios.determinism.ARITHMETIC_CONTEXT` — declared once for the
#: whole engine, so an atom, the framework and the composition layer cannot
#: drift apart.
RATIO_QUANTUM = Decimal("0.000001")

_ZERO = Decimal(0)
_ONE = Decimal(1)


def quantised_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """``numerator / denominator`` at a fixed, deterministic precision."""
    if denominator == 0:
        raise ContractViolationError(
            "cannot form a ratio with a zero denominator; a strategy must handle "
            "the degenerate case explicitly rather than divide"
        )
    with deterministic_arithmetic():
        return (numerator / denominator).quantize(RATIO_QUANTUM, rounding=ROUND_HALF_EVEN)


def unit_interval(value: Decimal) -> Decimal:
    """Clamp a derived measure into the 0..1 the envelope's ``strength`` requires."""
    if value < _ZERO:
        return _ZERO
    if value > _ONE:
        return _ONE
    return value


def proportion_beyond(excess: Decimal, reference: Decimal) -> Decimal:
    """A bounded 0..1 measure of ``excess`` relative to ``reference``.

    ``excess / (excess + reference)`` is used wherever an atom needs to express
    "how far beyond the declared threshold has this carried" without inventing
    a scaling constant that is nowhere declared: it is 0 at no excess, 0.5 at
    exactly one reference unit, and approaches 1 thereafter. The formula is
    documented per atom in ``docs/ATOMS.md`` so it is never a hidden choice.
    """
    excess = excess if excess > _ZERO else _ZERO
    denominator = excess + reference
    if denominator == 0:
        return _ZERO
    return unit_interval(quantised_ratio(excess, denominator))


@dataclass(frozen=True, slots=True)
class ParameterRequirement:
    """A parameter an atomic strategy requires its package to declare.

    ``minimum``/``maximum`` are the atom's own limits — the values outside
    which its condition has no defined meaning. They are checked in addition
    to whatever range the package declares for itself, and the tighter of the
    two always wins.
    """

    name: str
    type: ParameterType
    minimum: Optional[Decimal] = None
    maximum: Optional[Decimal] = None
    description: str = ""


@dataclass(frozen=True, slots=True)
class AtomVerdict:
    """One atom's assessment of the facts, before any state machinery.

    An atom says only what it can see. Whether that becomes MATCHED, ACTIVE,
    FORMING or anything else is decided by :class:`AtomicStrategy`, uniformly
    for every strategy.
    """

    holds: bool
    direction: Direction
    explanation: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    strength: Optional[Decimal] = None
    forming: bool = False
    invalidation: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ContractViolationError(
                "an atomic strategy must explain every verdict it returns"
            )
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


@dataclass(frozen=True, slots=True)
class AtomReading:
    """Everything an atom's condition is permitted to read.

    Deliberately narrower than :class:`~helios.protocols.EvaluationContext`:
    an atom sees its own declared window, its own declared parameters and its
    own previously published state. It is handed no registry, no sibling and
    no way to reach one.
    """

    frames: tuple[MarketFactFrame, ...]
    window: MarketFactWindow
    parameters: Mapping[str, Any]
    evaluated_at_utc: datetime
    previous_state: StrategyState
    previous_direction: Direction
    previous_strength: Optional[Decimal]
    previous_evidence: Mapping[str, Any]

    @property
    def latest(self) -> MarketFactFrame:
        """The bar being evaluated."""
        return self.frames[-1]

    @property
    def earlier(self) -> tuple[MarketFactFrame, ...]:
        """Every declared frame before the one being evaluated, oldest first."""
        return self.frames[:-1]

    @property
    def previous_is_live(self) -> bool:
        return self.previous_state in LIVE_STATES

    def parameter(self, name: str) -> Any:
        if name not in self.parameters:
            raise StrategySpecError(
                "strategy parameter was not resolved for this evaluation", parameter=name
            )
        return self.parameters[name]

    def decimal_parameter(self, name: str) -> Decimal:
        return to_decimal(self.parameter(name), field=name)

    def integer_parameter(self, name: str) -> int:
        value = self.parameter(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise StrategySpecError(
                "expected an integer parameter", parameter=name, value=repr(value)
            )
        return value

    def carried_decimal(self, name: str) -> Optional[Decimal]:
        """A decimal an earlier evaluation of THIS strategy published as evidence.

        Occurrence-scoped memory (the level a breakout cleared, the swing a
        location was near) travels in the published envelope and nowhere else.
        Canonical JSON renders decimals as text, so a value that has been
        round-tripped comes back as a string; both forms are accepted.
        """
        value = self.previous_evidence.get(name)
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (Decimal, int, str)):
            try:
                return to_decimal(value, field=name)
            except ContractViolationError:
                return None
        return None

    def carried_text(self, name: str) -> Optional[str]:
        value = self.previous_evidence.get(name)
        return value if isinstance(value, str) else None


class AtomicStrategy:
    """Base class for every atomic strategy.

    A subclass declares what it needs and implements :meth:`assess`. It does
    not implement :meth:`evaluate`: the published envelope, the state machine
    and the freshness rules are identical for every strategy by construction,
    which is what lets a chain compare two atoms it knows nothing about.

    Each atom consumes exactly ONE semantic input. That is a deliberate v1
    boundary rather than a limitation of the format: binding several inputs
    positionally would be ambiguous, and combining facts across semantic roles
    is the composition layer's job, not an atom's. A strategy needing more is
    a specification question for HSA, not something HELIOS should invent.
    """

    #: The ``strategy_id`` a package must carry for this implementation to be
    #: bound to it. Identity is the binding key; there is no separate
    #: "implementation" field in the package format to guess at.
    ATOM_NAME: ClassVar[str] = ""
    #: Market-fact fields the package's input must declare.
    REQUIRED_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: Fewest frames this condition can be assessed over.
    MIN_LOOKBACK: ClassVar[int] = 1
    #: Parameters the package must declare, with the atom's own limits.
    PARAMETERS: ClassVar[tuple[ParameterRequirement, ...]] = ()
    #: Whether this strategy expresses a directional bias at all.
    DIRECTIONAL: ClassVar[bool] = True
    #: Whether the condition is an EDGE — something that becomes true on one
    #: bar by changing, rather than a property a run of bars can each satisfy.
    #: An edge condition can never accumulate consecutive holds before it goes
    #: live, so a package asking it to persist for several frames before
    #: matching describes a strategy that could never match. That is a
    #: specification error and is refused rather than silently never firing.
    CONDITION_IS_AN_EDGE: ClassVar[bool] = False
    #: One line for ``docs/ATOMS.md`` and for operator-facing listings.
    SUMMARY: ClassVar[str] = ""

    def __init__(self, *, package: StrategyPackage, parameters: Mapping[str, Any]) -> None:
        if len(package.inputs) != 1:
            raise StrategySpecError(
                "an atomic strategy consumes exactly one semantic input",
                strategy_id=str(package.identity.strategy_id),
                declared_inputs=len(package.inputs),
            )
        requirement = package.inputs[0]
        self._package = package
        self._identity = StrategyIdentity(
            package.identity.strategy_id, package.identity.strategy_version
        )
        self._role: SemanticRole = requirement.role
        self._timeframe: Timeframe = requirement.timeframe
        self._lookback: int = requirement.lookback
        self._declared_fields: tuple[str, ...] = requirement.required_fields
        self._max_age_seconds: Optional[int] = requirement.max_age_seconds
        self._parameters: Mapping[str, Any] = MappingProxyType(dict(parameters))

    # ------------------------------------------------------------- identity

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    @property
    def package(self) -> StrategyPackage:
        return self._package

    @property
    def role(self) -> SemanticRole:
        """The semantic role the PACKAGE bound this strategy to."""
        return self._role

    @property
    def timeframe(self) -> Timeframe:
        """The timeframe the PACKAGE bound to that role."""
        return self._timeframe

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self._parameters

    def required_inputs(self) -> tuple[RequiredInput, ...]:
        return (
            RequiredInput(
                role=self._role,
                timeframe=self._timeframe,
                lookback=self._lookback,
                fields=self._declared_fields,
            ),
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"{type(self).__name__}({self._identity.canonical}, {self._role}/{self._timeframe.code})"

    # -------------------------------------------------------------- binding

    @classmethod
    def validate_binding(
        cls, package: StrategyPackage, parameters: Mapping[str, Any]
    ) -> None:
        """Assert this package and these parameters make a coherent strategy.

        The registry has already checked the things every atom shares. This
        hook is where an atom states a rule only it can know — for instance
        that its declared history must be at least as long as the range its
        own parameter asks it to measure. An override must call this
        implementation first.
        """
        if cls.CONDITION_IS_AN_EDGE and package.persistence.min_matched_frames > 1:
            raise StrategySpecError(
                "this strategy's condition is an edge: it becomes true by changing "
                "on one bar, so it can never hold for several frames before "
                "matching. The package as written could never match.",
                strategy_id=str(package.identity.strategy_id),
                min_matched_frames=package.persistence.min_matched_frames,
            )

    # ----------------------------------------------------------- evaluation

    def assess(self, reading: AtomReading) -> AtomVerdict:  # pragma: no cover - abstract
        """Decide whether this strategy's condition holds. Implemented per atom."""
        raise NotImplementedError

    def evaluate(self, context: EvaluationContext) -> StrategyStateEnvelope:
        """Produce the normalised state for one evaluation.

        Pure with respect to the context: same context in, same envelope out.
        No I/O, no clock of its own, no hidden state between calls.

        The whole evaluation runs inside the engine's fixed decimal context.
        An atom's condition is arithmetic over decimal facts — a wick as a
        fraction of a range, a close measured against a level plus a declared
        clearance — and under a coarse AMBIENT context that arithmetic would be
        rounded differently, or would raise, on a host that happened to install
        one. Fixing the context here rather than inside each atom means an
        atom's author cannot forget it, and means the concurrent path — where a
        worker thread starts from the interpreter's default context rather than
        its caller's — computes exactly what the sequential path computes.
        """
        with deterministic_arithmetic():
            window = self._resolve_window(context)
            policy = self._effective_policy(context.freshness_policy)
            freshness = require_fresh_frame(
                window.latest, policy, now_utc=context.evaluated_at_utc
            )
            frames = window.lookback(self._lookback)
            reading = self._read(frames, window, context)
            verdict = self.assess(reading)
            return self._publish(context, reading, verdict, freshness)

    # ------------------------------------------------------------- internals

    def _resolve_window(self, context: EvaluationContext) -> MarketFactWindow:
        window = context.window_for(self._role)
        if window.timeframe is not self._timeframe:
            raise ContractViolationError(
                "the window bound to this semantic role is on a different timeframe "
                "than the strategy package declared",
                strategy=self._identity.canonical,
                role=str(self._role),
                declared=self._timeframe.code,
                received=window.timeframe.code,
            )
        if window.instrument != context.instrument:
            raise ContractViolationError(
                "the window bound to this semantic role is for a different instrument",
                strategy=self._identity.canonical,
                role=str(self._role),
                expected=str(context.instrument),
                received=str(window.instrument),
            )
        return window

    def _effective_policy(self, policy: FreshnessPolicy) -> FreshnessPolicy:
        """Combine the deployment's policy with the package's own declaration.

        Configuration sets the ceiling; a package may only be STRICTER, never
        laxer. A package cannot talk a deployment into evaluating facts that
        deployment considers too old, and a deployment that tolerates a
        forming bar does not force one on a package that asked for closed
        frames only.
        """
        limit = policy.max_age_for(self._timeframe)
        declared = (
            timedelta(seconds=self._max_age_seconds)
            if self._max_age_seconds is not None
            else None
        )
        tightened = declared if declared is not None and declared < limit else None
        allow_incomplete = (
            policy.allow_incomplete_frames
            and self._package.timing.evaluate_on is EvaluateOn.EVERY_FRAME
        )
        if tightened is None and allow_incomplete == policy.allow_incomplete_frames:
            return policy
        overrides = dict(policy.overrides)
        if tightened is not None:
            overrides[self._timeframe] = tightened
        return FreshnessPolicy(
            max_age_multiplier=policy.max_age_multiplier,
            grace=policy.grace,
            allow_incomplete_frames=allow_incomplete,
            overrides=overrides,
        )

    def _read(
        self,
        frames: tuple[MarketFactFrame, ...],
        window: MarketFactWindow,
        context: EvaluationContext,
    ) -> AtomReading:
        previous = context.previous
        if previous is not None:
            self._assert_own_envelope(previous)
        return AtomReading(
            frames=frames,
            window=window,
            parameters=self._parameters,
            evaluated_at_utc=context.evaluated_at_utc,
            previous_state=previous.state if previous else StrategyState.DORMANT,
            previous_direction=previous.direction if previous else self._resting_direction(),
            previous_strength=previous.strength if previous else None,
            previous_evidence=previous.evidence if previous else {},
        )

    def _assert_own_envelope(self, previous: StrategyStateEnvelope) -> None:
        """A strategy is handed its OWN last envelope or none at all."""
        if (
            str(previous.strategy_id) != str(self._identity.strategy_id)
            or previous.strategy_version != self._identity.strategy_version
            or previous.kind is not EnvelopeKind.ATOMIC
        ):
            raise ContractViolationError(
                "previous envelope belongs to a different strategy; a strategy may "
                "only be handed its own published state",
                strategy=self._identity.canonical,
                received=f"{previous.strategy_id}@{previous.strategy_version}",
                received_kind=previous.kind.value,
            )

    def _resting_direction(self) -> Direction:
        """What direction means when the condition does not hold.

        NEUTRAL for a direction-aware strategy that currently finds no bias;
        NONE for a strategy that is not directional at all. The contract keeps
        those distinct and so does this.
        """
        return Direction.NEUTRAL if self.DIRECTIONAL else Direction.NONE

    def _hold_run(self, reading: AtomReading) -> int:
        value = reading.previous_evidence.get(HOLD_RUN_KEY)
        if isinstance(value, bool) or value is None:
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _expiry_instant(self, first_matched_at_utc: Optional[datetime]) -> Optional[datetime]:
        if first_matched_at_utc is None:
            return None
        expiry = self._package.expiry
        if expiry.mode is ExpiryMode.NEVER:
            return None
        if expiry.mode is ExpiryMode.FRAMES:
            return first_matched_at_utc + self._timeframe.duration * int(expiry.frames or 0)
        return first_matched_at_utc + timedelta(seconds=int(expiry.duration_seconds or 0))

    def _previous_lifecycle(
        self, previous: Optional[StrategyStateEnvelope]
    ) -> Optional[LifecycleTimestamps]:
        if previous is None:
            return None
        return LifecycleTimestamps(
            last_evaluated_at_utc=previous.last_evaluated_at_utc,
            first_matched_at_utc=previous.first_matched_at_utc,
            last_matched_at_utc=previous.last_matched_at_utc,
            active_since_utc=previous.active_since_utc,
        )

    def _resolve_state(
        self,
        reading: AtomReading,
        verdict: AtomVerdict,
        previous_lifecycle: Optional[LifecycleTimestamps],
    ) -> tuple[StrategyState, int, str]:
        """Map a verdict onto a state that respects the legal transition table.

        The order below is the whole state model. It is applied identically to
        every atomic strategy, so two strategies in the same state mean the
        same thing.
        """
        previous_state = reading.previous_state
        run = self._hold_run(reading)
        persistence = self._package.persistence

        # A resolved occurrence rearms only through DORMANT, and the two ways
        # of resolving rearm differently on purpose.
        #
        # EXPIRED means time ran out on a condition that may well still be
        # true. Rearming immediately would re-match the very occurrence that
        # just aged out and make the declared expiry meaningless, so an
        # expired occurrence stays expired for as long as its own condition
        # holds in the same direction.
        #
        # INVALID means the condition broke. There is nothing left to hold
        # open, so the strategy rearms on the next evaluation and re-assesses
        # from scratch — which is what lets a reversed condition match in the
        # other direction shortly afterwards.
        if previous_state is StrategyState.EXPIRED:
            if verdict.holds and verdict.direction is reading.previous_direction:
                return (
                    StrategyState.EXPIRED,
                    0,
                    "the expired occurrence is still true in the same direction; it "
                    "cannot restart until the condition clears",
                )
            return (
                StrategyState.DORMANT,
                0,
                "the expired occurrence has cleared; the strategy is rearmed",
            )
        if previous_state is StrategyState.INVALID:
            return (
                StrategyState.DORMANT,
                0,
                "the invalidated occurrence is recorded; the strategy is rearmed and "
                "re-assesses the condition from DORMANT",
            )

        expires_at = self._expiry_instant(
            previous_lifecycle.first_matched_at_utc if previous_lifecycle else None
        )
        if (
            previous_state in LIVE_STATES
            and expires_at is not None
            and reading.evaluated_at_utc > expires_at
        ):
            return (
                StrategyState.EXPIRED,
                0,
                "the match aged past its declared validity window without being "
                "invalidated",
            )

        if verdict.invalidation is not None and previous_state in (
            LIVE_STATES | {StrategyState.FORMING}
        ):
            return StrategyState.INVALID, 0, verdict.invalidation

        if verdict.holds:
            run += 1
            if previous_state in LIVE_STATES:
                if (
                    persistence.weakening_enabled
                    and verdict.strength is not None
                    and reading.previous_strength is not None
                    and verdict.strength < reading.previous_strength
                ):
                    return (
                        StrategyState.WEAKENING,
                        run,
                        "the match still holds but its declared strength measure is "
                        "lower than at the previous evaluation",
                    )
                return (
                    StrategyState.ACTIVE,
                    run,
                    "the match was observed before and still holds",
                )
            if run >= persistence.min_matched_frames:
                return (
                    StrategyState.MATCHED,
                    run,
                    "the full condition became true on this evaluation",
                )
            return (
                StrategyState.FORMING,
                run,
                f"the condition holds but has not yet held for the declared "
                f"{persistence.min_matched_frames} frames",
            )

        if previous_state in LIVE_STATES:
            return (
                StrategyState.INVALID,
                0,
                "the condition no longer holds; the match is void",
            )
        if verdict.forming:
            return (
                StrategyState.FORMING,
                0,
                "a declared precondition holds; the full condition does not yet",
            )
        return StrategyState.DORMANT, 0, "no part of the condition currently holds"

    def _published_direction(
        self, state: StrategyState, reading: AtomReading, verdict: AtomVerdict
    ) -> Direction:
        """Which direction the envelope carries for this state.

        A live state publishes the strategy's current bias. A resolved state
        publishes the bias of the occurrence that just ended, so a consumer can
        see WHAT was invalidated or what expired rather than a bare NEUTRAL.
        Everything else publishes the resting direction.
        """
        if state in LIVE_STATES:
            return verdict.direction
        if state in RESOLVED_STATES and reading.previous_direction.is_directional:
            return reading.previous_direction
        return self._resting_direction()

    def _publish(
        self,
        context: EvaluationContext,
        reading: AtomReading,
        verdict: AtomVerdict,
        freshness: FreshnessVerdict,
    ) -> StrategyStateEnvelope:
        previous_lifecycle = self._previous_lifecycle(context.previous)
        state, run, state_reason = self._resolve_state(reading, verdict, previous_lifecycle)

        if context.previous is not None:
            # Fails loudly if the state machine above ever proposes something
            # the documented table forbids.
            transition(
                reading.previous_state,
                state,
                at_utc=context.evaluated_at_utc,
                reason=state_reason,
            )

        lifecycle = advance_lifecycle(previous_lifecycle, state, context.evaluated_at_utc)
        is_live = state in LIVE_STATES

        evidence: dict[str, Any] = dict(verdict.evidence)
        evidence[HOLD_RUN_KEY] = run

        explanation = f"{verdict.explanation}; {state_reason}"
        validity = Validity(
            valid_from_utc=lifecycle.first_matched_at_utc,
            valid_until_utc=self._expiry_instant(lifecycle.first_matched_at_utc),
            reason=state_reason if state in RESOLVED_STATES else None,
        )
        return StrategyStateEnvelope.with_lifecycle(
            lifecycle,
            kind=EnvelopeKind.ATOMIC,
            strategy_id=self._identity.strategy_id,
            strategy_version=self._identity.strategy_version,
            instrument=context.instrument,
            timeframe=self._timeframe,
            semantic_role=self._role,
            state=state,
            direction=self._published_direction(state, reading, verdict),
            strength=verdict.strength if is_live else None,
            evidence=evidence,
            explanation=explanation,
            validity=validity,
            inputs=(InputFreshness.from_verdict(freshness, semantic_role=self._role),),
        )
