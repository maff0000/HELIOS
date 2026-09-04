"""HELIOS — deterministic, execution-blind strategy-state engine.

HELIOS consumes HERMES market facts, evaluates independent atomic strategies
and governed chains, and publishes normalised strategy state for FALCON.

It is execution-blind by construction: nothing in this package can observe
downstream activity, so identical ordered facts and identical definitions
always yield identical state.

This package is the WI-1 contract kernel — the vocabulary every later work
item consumes. It intentionally contains no strategy logic.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
