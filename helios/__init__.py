"""HELIOS — deterministic, execution-blind strategy-state engine.

HELIOS consumes HERMES market facts, evaluates independent atomic strategies
and governed chains, and publishes normalised strategy state for FALCON.

It is execution-blind by construction: nothing in this package can observe
downstream activity, so identical ordered facts and identical definitions
always yield identical state.

The package holds the contract kernel — the vocabulary everything else speaks
— and the engine built on it: :mod:`helios.contracts`, :mod:`helios.spec`,
:mod:`helios.strategies` (the atomic framework and the proof atoms),
:mod:`helios.composition` (the chain engine), :mod:`helios.publish` (the FALCON
publication boundary), :mod:`helios.integration`, :mod:`helios.hermes`,
:mod:`helios.config` and :mod:`helios.observability`.

Absent by design, and enforced: NEO decision logic, TRON execution, account
risk, broker integration, backtesting, strategy auto-tuning, a dashboard, an
evidence store (CER owns that), and any reusable indicator library (HERMES owns
indicators).
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
