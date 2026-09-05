"""Direction resolution and compatibility for chains.

Two questions, deliberately separated:

1. **What direction does the chain itself publish?** A chain package declares
   ``resolution: FROM_COMPONENTS``, so the chain has no direction of its own —
   it adopts one from an *anchor* component.
2. **Is each component's direction acceptable given that?** Each component
   declares ``SAME``, ``OPPOSITE`` or ``ANY``.

The two are resolved in that order, in one pass each, which is what breaks the
otherwise circular dependency between "the chain's direction" and "which
components count". Direction is resolved from the components that hold on
their own terms; compatibility is then applied to every component.

The anchor is the first component, in canonical chain order, that holds on its
own terms, declares ``SAME`` and publishes ``LONG`` or ``SHORT``. Only a
``SAME`` component can define the chain's direction: a component declared
``OPPOSITE`` states the inverse of the chain's bias by definition, and one
declared ``ANY`` states nothing about it, so neither can be the anchor without
inverting or inventing the chain's meaning.
"""

from __future__ import annotations

from typing import Iterable, Optional

from helios.composition.outcomes import ComponentOutcome, ComponentReason
from helios.contracts.state import Direction
from helios.spec.model import DirectionMode, DirectionRelationship, DirectionSpec


def resolve_chain_direction(
    spec: DirectionSpec, ordered_outcomes: Iterable[ComponentOutcome]
) -> Direction:
    """The direction the chain publishes, given which components hold.

    * A ``NON_DIRECTIONAL`` chain publishes ``NONE`` — direction is not
      applicable to it, which is a different statement from having no bias.
    * A ``DIRECTIONAL`` chain adopts its anchor's ``LONG``/``SHORT``.
    * A ``DIRECTIONAL`` chain with no directional anchor publishes ``NEUTRAL``:
      it is direction-aware and currently has no resolvable bias.
    """
    if spec.mode is DirectionMode.NON_DIRECTIONAL:
        return Direction.NONE
    anchor = find_anchor(ordered_outcomes)
    if anchor is None:
        return Direction.NEUTRAL
    direction = anchor.observed_direction
    return direction if direction is not None else Direction.NEUTRAL


def find_anchor(ordered_outcomes: Iterable[ComponentOutcome]) -> Optional[ComponentOutcome]:
    """The component whose direction the chain adopts, or ``None``."""
    for outcome in ordered_outcomes:
        if not outcome.satisfied:
            continue
        if outcome.relationship is not DirectionRelationship.SAME:
            continue
        direction = outcome.observed_direction
        if direction is not None and direction.is_directional:
            return outcome
    return None


def is_compatible(
    relationship: DirectionRelationship,
    component_direction: Direction,
    chain_direction: Direction,
) -> bool:
    """Whether a component's direction stands in its declared relationship.

    * ``SAME`` — identical direction values. Identity rather than "both LONG or
      both SHORT" so that a ``NEUTRAL`` chain is satisfied only by ``NEUTRAL``
      components and a ``NONE`` chain only by non-directional ones. A
      ``NEUTRAL`` component never counts as agreeing with a ``LONG`` chain.
    * ``OPPOSITE`` — both directional and opposed. ``NEUTRAL``/``NONE`` cannot
      oppose anything, so they never satisfy ``OPPOSITE``.
    * ``ANY`` — no constraint. The component participates on state alone.
    """
    if relationship is DirectionRelationship.ANY:
        return True
    if relationship is DirectionRelationship.SAME:
        return component_direction is chain_direction
    return component_direction.opposes(chain_direction)


def apply_direction(
    outcomes: Iterable[ComponentOutcome], chain_direction: Direction
) -> tuple[ComponentOutcome, ...]:
    """Mark every component whose direction breaks its declared relationship."""
    checked: list[ComponentOutcome] = []
    for outcome in outcomes:
        if not outcome.satisfied:
            checked.append(outcome)
            continue
        observed = outcome.observed_direction
        if observed is None:  # pragma: no cover - satisfied implies an envelope
            checked.append(outcome)
            continue
        if is_compatible(outcome.relationship, observed, chain_direction):
            checked.append(outcome)
            continue
        checked.append(
            outcome.failing(
                ComponentReason.DIRECTION_INCOMPATIBLE,
                f"component direction {observed} is not {outcome.relationship} "
                f"the chain direction {chain_direction}",
            )
        )
    return tuple(checked)
