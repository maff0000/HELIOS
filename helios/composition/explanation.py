"""Rendering the chain's explanation.

The PID requires an explicit explanation of why a chain matched **or did not
match**. A non-match must be as explainable as a match: which component
failed, and why.

The explanation is rendered from the same :class:`ChainAssessment` that
produced the published state and the published component provenance, so the
three can never disagree. It is deterministic — no set iteration, no clock, no
locale — because two identical evaluations must produce byte-identical output.
"""

from __future__ import annotations

from helios.composition.outcomes import ChainAssessment
from helios.contracts.identity import StrategyIdentity
from helios.contracts.state import StrategyState
from helios.spec.model import ChainPrimitive


def render_explanation(
    *,
    identity: StrategyIdentity,
    primitive: ChainPrimitive,
    instrument: str,
    state: StrategyState,
    assessment: ChainAssessment,
    resolution_note: str,
) -> str:
    """One deterministic sentence sequence explaining this evaluation."""
    headline = (
        f"{primitive} chain {identity.canonical} on {instrument} "
        f"{'matched' if assessment.satisfied else 'did not match'} "
        f"{assessment.direction}"
    )
    parts = [headline]

    if assessment.satisfied:
        detail = "; ".join(outcome.describe() for outcome in assessment.outcomes)
    else:
        failures = assessment.unsatisfied
        detail = "; ".join(outcome.describe() for outcome in failures)
        holding = [outcome for outcome in assessment.outcomes if outcome.satisfied]
        if holding:
            detail += ". Still holding: " + "; ".join(
                outcome.describe() for outcome in holding
            )
    if detail:
        parts.append(detail)

    for reason in assessment.chain_reasons:
        parts.append(reason)

    parts.append(
        f"{assessment.satisfied_count} of {assessment.declared_count} components satisfied"
    )
    parts.append(f"published state {state}: {resolution_note}")
    return ". ".join(part.rstrip(". ") for part in parts if part) + "."
