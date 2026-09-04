"""Evaluating many independent strategies over the same facts.

Two invariants the PID requires and this module is where they are kept:

**One failed evaluation cannot corrupt unrelated state.** A strategy that
raises is contained: it resolves to an explicit ``INVALID`` envelope naming
what went wrong, and every sibling still produces its own correct state from
the same facts. Nothing is retried, nothing is silently skipped, and no
sibling ever learns that anything failed.

**Reproducible from identical ordered inputs.** Results are returned in the
order the evaluators were supplied, never in completion order, so running the
same set concurrently and sequentially produces byte-identical output.

Concurrency is safe here by construction rather than by locking: windows,
frames and envelopes are all frozen, each strategy is handed its own context
and its own previous envelope, and an atom has no reference through which it
could reach a sibling.

A note on the contained failure envelope. ``INVALID`` is the contract's word
for "this state is void", and it is what a failed evaluation must publish: the
alternative, ``DORMANT``, would assert "evaluated, nothing holds" when in fact
nothing was evaluated — a silent default of exactly the kind the PID forbids.
A containment envelope is therefore published without consulting the legal
transition table, because an evaluation failure is not a state change of the
strategy's condition; the table governs condition-driven transitions, which is
all any strategy's own evaluation can produce.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from helios.contracts._tokens import Instrument, SemanticRole
from helios.contracts.freshness import FreshnessPolicy
from helios.contracts.identity import StrategyIdentity
from helios.contracts.lifecycle import LifecycleTimestamps, advance_lifecycle
from helios.contracts.output import (
    EnvelopeKind,
    StrategyStateEnvelope,
    Validity,
)
from helios.contracts.state import Direction, StrategyState
from helios.contracts.window import MarketFactWindow
from helios.protocols import EvaluationContext, StrategyEvaluator

#: Evidence key naming the error class behind a contained failure.
FAILURE_KEY = "contained_failure_type"


@dataclass(frozen=True, slots=True)
class StrategyOutcome:
    """One strategy's published state, and whether it had to be contained."""

    identity: StrategyIdentity
    envelope: StrategyStateEnvelope
    failure_type: Optional[str] = None
    failure_detail: Optional[str] = None

    @property
    def contained(self) -> bool:
        """True when the strategy raised and its state was published as void."""
        return self.failure_type is not None


ContextFor = Callable[[StrategyEvaluator], EvaluationContext]


def shared_facts_context(
    *,
    instrument: Instrument,
    evaluated_at_utc: datetime,
    windows: Mapping[SemanticRole, MarketFactWindow],
    freshness_policy: FreshnessPolicy,
    previous: Optional[Mapping[str, StrategyStateEnvelope]] = None,
) -> ContextFor:
    """Build contexts for many strategies over ONE set of market facts.

    Every strategy sees the same frozen windows and asks for its own semantic
    role; each is handed only its own previous envelope, keyed by canonical
    identity. There is no key by which one strategy could ask for another's.
    """
    published = dict(previous or {})

    def context_for(evaluator: StrategyEvaluator) -> EvaluationContext:
        identity = evaluator.identity
        return EvaluationContext(
            instrument=instrument,
            evaluated_at_utc=evaluated_at_utc,
            windows=windows,
            parameters=getattr(evaluator, "parameters", {}),
            freshness_policy=freshness_policy,
            previous=published.get(identity.canonical),
        )

    return context_for


def evaluate_sequentially(
    evaluators: Sequence[StrategyEvaluator], context_for: ContextFor
) -> tuple[StrategyOutcome, ...]:
    """Evaluate in the given order, containing any failure."""
    prepared = _prepare(evaluators, context_for)
    return tuple(
        _contained(evaluator, context) for evaluator, context in prepared
    )


def evaluate_concurrently(
    evaluators: Sequence[StrategyEvaluator],
    context_for: ContextFor,
    *,
    max_workers: Optional[int] = None,
) -> tuple[StrategyOutcome, ...]:
    """Evaluate many strategies at once, containing any failure.

    ``max_workers`` is a runtime concern supplied by the caller from validated
    configuration. ``None`` means "let the platform decide" — it is the absence
    of a policy, not a policy value baked into source.
    """
    prepared = _prepare(evaluators, context_for)
    if not prepared:
        return ()
    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="helios-strategy"
    ) as pool:
        futures = [
            pool.submit(_contained, evaluator, context) for evaluator, context in prepared
        ]
        return tuple(future.result() for future in futures)


# ----------------------------------------------------------------- internals


def _prepare(
    evaluators: Sequence[StrategyEvaluator], context_for: ContextFor
) -> tuple[tuple[StrategyEvaluator, EvaluationContext], ...]:
    """Describe and contextualise every evaluator before any of them runs.

    Identity, declared inputs and context construction happen here, in the
    caller's thread. An evaluator that cannot describe itself was never validly
    built, and that is a loud failure of the caller's wiring rather than a
    strategy-level failure to contain.
    """
    seen: set[str] = set()
    prepared: list[tuple[StrategyEvaluator, EvaluationContext]] = []
    for evaluator in evaluators:
        identity = evaluator.identity
        if identity.canonical in seen:
            raise ValueError(
                f"the same strategy identity was supplied twice: {identity.canonical}"
            )
        seen.add(identity.canonical)
        prepared.append((evaluator, context_for(evaluator)))
    return tuple(prepared)


def _contained(
    evaluator: StrategyEvaluator, context: EvaluationContext
) -> StrategyOutcome:
    identity = evaluator.identity
    try:
        envelope = evaluator.evaluate(context)
    except Exception as error:  # noqa: BLE001 - containment is the point
        return StrategyOutcome(
            identity=identity,
            envelope=_void_envelope(evaluator, context, error),
            failure_type=type(error).__name__,
            failure_detail=str(error),
        )
    return StrategyOutcome(identity=identity, envelope=envelope)


def _void_envelope(
    evaluator: StrategyEvaluator, context: EvaluationContext, error: Exception
) -> StrategyStateEnvelope:
    """The explicit INVALID state published when a strategy could not evaluate."""
    identity = evaluator.identity
    declared = evaluator.required_inputs()
    timeframe = declared[0].timeframe if declared else None
    role = declared[0].role if declared else None
    previous = context.previous
    previous_lifecycle: Optional[LifecycleTimestamps] = None
    if previous is not None:
        previous_lifecycle = LifecycleTimestamps(
            last_evaluated_at_utc=previous.last_evaluated_at_utc,
            first_matched_at_utc=previous.first_matched_at_utc,
            last_matched_at_utc=previous.last_matched_at_utc,
            active_since_utc=previous.active_since_utc,
        )
    lifecycle = advance_lifecycle(
        previous_lifecycle, StrategyState.INVALID, context.evaluated_at_utc
    )
    reason = "the strategy raised during evaluation; no state can be asserted"
    evidence: dict[str, Any] = {FAILURE_KEY: type(error).__name__}
    return StrategyStateEnvelope.with_lifecycle(
        lifecycle,
        kind=EnvelopeKind.ATOMIC,
        strategy_id=identity.strategy_id,
        strategy_version=identity.strategy_version,
        instrument=context.instrument,
        timeframe=timeframe,
        semantic_role=role,
        state=StrategyState.INVALID,
        direction=previous.direction if previous is not None else Direction.NEUTRAL,
        strength=None,
        evidence=evidence,
        explanation=f"{reason}: {error}",
        validity=Validity(
            valid_from_utc=lifecycle.first_matched_at_utc,
            valid_until_utc=None,
            reason=reason,
        ),
        inputs=(),
    )
