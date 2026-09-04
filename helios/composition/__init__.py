"""The composition layer: chains over normalised atomic-strategy outputs.

This package combines the *published state* of atomic strategies. It imports
no concrete strategy and has no way to reach one — its input is the normalised
output contract and nothing else. That is the PID's separation made
structural: atomic strategies do not know other strategies exist, and the
layer that composes them knows only what they publish.

The four canonical v1 primitives are ``ALL``, ``ANY``, ``SEQUENCE`` and
``CONTEXT_TRIGGER``. Chains consume atomic strategies directly; recursive
chain-of-chain composition is refused, per the PID.

See ``docs/COMPOSITION.md`` for the exact semantics of each primitive and of
direction, ordering, persistence and expiry.
"""

from helios.composition.direction import (
    apply_direction,
    find_anchor,
    is_compatible,
    resolve_chain_direction,
)
from helios.composition.engine import ChainEngine
from helios.composition.explanation import render_explanation
from helios.composition.outcomes import (
    ChainAssessment,
    ComponentOutcome,
    ComponentReason,
)
from helios.composition.primitives import PrimitiveResult, evaluate_primitive

__all__ = [
    "ChainAssessment",
    "ChainEngine",
    "ComponentOutcome",
    "ComponentReason",
    "PrimitiveResult",
    "apply_direction",
    "evaluate_primitive",
    "find_anchor",
    "is_compatible",
    "render_explanation",
    "resolve_chain_direction",
]
