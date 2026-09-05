"""The four canonical v1 composition primitives.

Each primitive receives the component outcomes in *canonical chain order* and
returns them, possibly with further components marked unsatisfied, plus
chain-level reasons. A primitive never raises on market conditions: an
unsatisfiable chain is a well-defined verdict with an explanation, not an
error.

Canonical order is the primitive's own notion of order and nothing else:

* ``SEQUENCE`` — ascending ``sequence_index`` (the spec guarantees these are
  unique and contiguous from 0);
* ``CONTEXT_TRIGGER`` — the ``CONTEXT`` component, then any intermediate
  components in declared order, then the ``TRIGGER`` component;
* ``ALL`` / ``ANY`` — the order the package declared.

No primitive consults a timeframe to decide order. Timeframes reach a chain
only through the package's own role bindings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from helios.clock import to_iso8601_utc
from helios.composition.outcomes import ComponentOutcome, ComponentReason
from helios.contracts._tokens import ROLE_CONTEXT, ROLE_TRIGGER
from helios.spec.model import ChainPrimitive, ChainSpec


@dataclass(frozen=True, slots=True)
class PrimitiveResult:
    """A primitive's verdict for one evaluation."""

    satisfied: bool
    outcomes: tuple[ComponentOutcome, ...]
    reasons: tuple[str, ...]


def evaluate_primitive(
    chain: ChainSpec, outcomes: Sequence[ComponentOutcome]
) -> PrimitiveResult:
    """Apply the chain's primitive to already direction-checked outcomes."""
    primitive = chain.primitive
    if primitive is ChainPrimitive.ALL:
        return _evaluate_all(outcomes)
    if primitive is ChainPrimitive.ANY:
        return _evaluate_any(outcomes)
    if primitive is ChainPrimitive.SEQUENCE:
        return _evaluate_sequence(outcomes, chain.ordering_window_seconds)
    return _evaluate_context_trigger(outcomes)


def _evaluate_all(outcomes: Sequence[ComponentOutcome]) -> PrimitiveResult:
    """Every declared component must hold. Nothing else is considered."""
    satisfied = all(outcome.satisfied for outcome in outcomes)
    reasons: tuple[str, ...] = ()
    if not satisfied:
        missing = [outcome.label for outcome in outcomes if not outcome.satisfied]
        reasons = (
            f"ALL requires every one of {len(outcomes)} components; "
            f"{len(missing)} did not hold",
        )
    return PrimitiveResult(satisfied=satisfied, outcomes=tuple(outcomes), reasons=reasons)


def _evaluate_any(outcomes: Sequence[ComponentOutcome]) -> PrimitiveResult:
    """At least one declared component must hold.

    A component that does not hold is not a failure of the chain — it simply
    contributes nothing. Its own reason is still published, so an ``ANY``
    chain still explains every component.
    """
    holding = [outcome for outcome in outcomes if outcome.satisfied]
    satisfied = bool(holding)
    reasons: tuple[str, ...] = ()
    if satisfied:
        reasons = (
            f"ANY is satisfied by {len(holding)} of {len(outcomes)} components",
        )
    else:
        reasons = (f"ANY requires at least one of {len(outcomes)} components to hold",)
    return PrimitiveResult(satisfied=satisfied, outcomes=tuple(outcomes), reasons=reasons)


def _evaluate_sequence(
    outcomes: Sequence[ComponentOutcome], window_seconds: Optional[int]
) -> PrimitiveResult:
    """Every component must hold, and must have matched in the declared order.

    Ordering uses each component's ``first_matched_at_utc`` — when this
    occurrence of its condition began — not its last match, which advances
    while the component stays live.

    **Ties are in order.** Two consecutive components sharing an identical
    match instant satisfy the ordering. HELIOS evaluates on frame boundaries
    and a 4H close is also a 5M close, so a genuinely simultaneous match across
    two timeframes is ordinary, not a violation. Requiring a strict increase
    would make such a chain unmatchable for reasons that have nothing to do
    with the strategy. Only a *later* component matching strictly *earlier*
    than the one declared before it breaks the order; the engine never
    reorders components to make a sequence fit.

    The window is measured between the earliest and latest match instants and
    is inclusive: a span exactly equal to ``ordering_window_seconds`` is
    inside the window.
    """
    checked = list(outcomes)
    reasons: list[str] = []

    for index, outcome in enumerate(checked):
        if outcome.satisfied and outcome.matched_at_utc is None:
            checked[index] = outcome.failing(
                ComponentReason.NO_MATCH_INSTANT,
                f"state {outcome.observed_state} published no first match instant, "
                "so its place in the order cannot be established",
            )

    if not all(outcome.satisfied for outcome in checked):
        reasons.append(
            f"SEQUENCE requires all {len(checked)} components to hold in the "
            "declared order"
        )
        return PrimitiveResult(satisfied=False, outcomes=tuple(checked), reasons=tuple(reasons))

    instants: list[datetime] = [outcome.matched_at_utc for outcome in checked]  # type: ignore[misc]
    ordered = True
    for index in range(1, len(checked)):
        if instants[index] < instants[index - 1]:
            ordered = False
            checked[index] = checked[index].failing(
                ComponentReason.OUT_OF_DECLARED_ORDER,
                f"matched at {to_iso8601_utc(instants[index])}, before "
                f"{checked[index - 1].label} matched at "
                f"{to_iso8601_utc(instants[index - 1])}",
            )

    earliest = min(instants)
    latest = max(instants)
    span = latest - earliest
    within_window = True
    if window_seconds is not None and span > timedelta(seconds=window_seconds):
        # The components each hold; it is their *combination* that does not fit
        # the declared window. Marking them individually unsatisfied would
        # misreport the chain as having nothing at all, so the lapse is a
        # chain-level reason and each component keeps its own true verdict.
        within_window = False
        reasons.append(
            f"the components span {int(span.total_seconds())}s, which exceeds the "
            f"declared ordering window of {window_seconds}s "
            f"({to_iso8601_utc(earliest)} to {to_iso8601_utc(latest)})"
        )
    if not ordered:
        reasons.append("the components did not match in their declared order")
    satisfied = ordered and within_window
    if satisfied:
        reasons.append(
            f"all {len(checked)} components matched in the declared order within "
            f"{int(span.total_seconds())}s of one another"
        )
    return PrimitiveResult(satisfied=satisfied, outcomes=tuple(checked), reasons=tuple(reasons))


def _evaluate_context_trigger(outcomes: Sequence[ComponentOutcome]) -> PrimitiveResult:
    """A context must be established and still valid when the trigger matches.

    The spec guarantees exactly one ``CONTEXT`` and one ``TRIGGER`` component.
    Any component in between is an additional condition that must also hold;
    declaring a component and then ignoring it would be a silent pass.

    Three rules beyond "both hold":

    1. the context's match instant must not be later than the trigger's — a
       context established *after* its trigger did not frame that trigger;
    2. if the context publishes its own ``valid_until_utc``, the trigger's
       match instant must not be after it — an expired context frames nothing;
    3. the context must still be holding at this evaluation, which is already
       covered by its own state and validity checks.

    Simultaneous instants are accepted, for the same reason ties are accepted
    in a SEQUENCE.
    """
    checked = list(outcomes)
    reasons: list[str] = []
    context_index = _index_of_role(checked, str(ROLE_CONTEXT))
    trigger_index = _index_of_role(checked, str(ROLE_TRIGGER))

    if not all(outcome.satisfied for outcome in checked):
        reasons.append(
            "CONTEXT_TRIGGER requires the context, the trigger and every "
            "intermediate component to hold"
        )
        return PrimitiveResult(satisfied=False, outcomes=tuple(checked), reasons=tuple(reasons))

    context = checked[context_index]
    trigger = checked[trigger_index]
    context_at = context.matched_at_utc
    trigger_at = trigger.matched_at_utc

    for index, outcome, other in (
        (context_index, context, context_at),
        (trigger_index, trigger, trigger_at),
    ):
        if other is None:
            checked[index] = outcome.failing(
                ComponentReason.NO_MATCH_INSTANT,
                f"state {outcome.observed_state} published no first match instant, "
                "so the context/trigger relationship cannot be established",
            )
    if not all(outcome.satisfied for outcome in checked):
        reasons.append("the context/trigger relationship could not be established")
        return PrimitiveResult(satisfied=False, outcomes=tuple(checked), reasons=tuple(reasons))

    assert context_at is not None and trigger_at is not None

    if context_at > trigger_at:
        checked[context_index] = context.failing(
            ComponentReason.CONTEXT_NOT_ESTABLISHED_FIRST,
            f"the context matched at {to_iso8601_utc(context_at)}, after the trigger "
            f"matched at {to_iso8601_utc(trigger_at)}",
        )
        reasons.append("the context was not established before the trigger matched")
        return PrimitiveResult(satisfied=False, outcomes=tuple(checked), reasons=tuple(reasons))

    context_envelope = context.envelope
    assert context_envelope is not None
    context_until = context_envelope.validity.valid_until_utc
    if context_until is not None and trigger_at > context_until:
        checked[context_index] = context.failing(
            ComponentReason.CONTEXT_VALIDITY_LAPSED,
            f"the context was valid until {to_iso8601_utc(context_until)} but the "
            f"trigger matched at {to_iso8601_utc(trigger_at)}",
        )
        reasons.append("the context was no longer valid when the trigger matched")
        return PrimitiveResult(satisfied=False, outcomes=tuple(checked), reasons=tuple(reasons))

    reasons.append(
        f"the context was established at {to_iso8601_utc(context_at)} and was still "
        f"valid when the trigger matched at {to_iso8601_utc(trigger_at)}"
    )
    return PrimitiveResult(satisfied=True, outcomes=tuple(checked), reasons=tuple(reasons))


def _index_of_role(outcomes: Sequence[ComponentOutcome], role: str) -> int:
    for index, outcome in enumerate(outcomes):
        if outcome.role is not None and str(outcome.role) == role:
            return index
    raise AssertionError(  # pragma: no cover - the package spec guarantees the role
        f"CONTEXT_TRIGGER chain has no component in the {role} role"
    )
