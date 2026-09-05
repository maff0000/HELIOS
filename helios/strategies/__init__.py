"""The atomic strategy framework.

An atomic strategy is a small, independent, execution-blind condition over
HERMES facts. It declares what it needs, evaluates deterministically, owns its
own state and publishes the normalised output contract. It does not know that
any other strategy exists.

This module exposes the framework only. The atoms themselves are reached
through :mod:`helios.strategies.catalogue`, which is deliberately NOT imported
here: importing one atom must not pull its siblings into the process, and a
convenient re-export from this package would silently destroy that.
"""

from helios.strategies.base import (
    HOLD_RUN_KEY,
    RATIO_QUANTUM,
    AtomicStrategy,
    AtomReading,
    AtomVerdict,
    ParameterRequirement,
    proportion_beyond,
    quantised_ratio,
    unit_interval,
)
from helios.strategies.evaluation import (
    FAILURE_KEY,
    StrategyOutcome,
    evaluate_concurrently,
    evaluate_sequentially,
    shared_facts_context,
)
from helios.strategies.registry import AtomRegistry

__all__ = [
    "FAILURE_KEY",
    "HOLD_RUN_KEY",
    "RATIO_QUANTUM",
    "AtomRegistry",
    "AtomReading",
    "AtomVerdict",
    "AtomicStrategy",
    "ParameterRequirement",
    "StrategyOutcome",
    "evaluate_concurrently",
    "evaluate_sequentially",
    "proportion_beyond",
    "quantised_ratio",
    "shared_facts_context",
    "unit_interval",
]
