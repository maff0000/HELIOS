"""HELIOS error taxonomy.

Every failure mode in HELIOS is explicit and loud. Nothing in this package
silently defaults, silently zeroes, or silently substitutes a value for a fact
it does not have. Each error carries a structured ``context`` mapping so that
the JSON logger can emit machine-readable failure detail.
"""

from __future__ import annotations

from typing import Any, Mapping


class HeliosError(Exception):
    """Base class for every HELIOS failure."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: Mapping[str, Any] = dict(context)

    def __str__(self) -> str:  # pragma: no cover - trivial
        if not self.context:
            return self.message
        detail = ", ".join(f"{key}={value!r}" for key, value in sorted(self.context.items()))
        return f"{self.message} ({detail})"


class ContractViolationError(HeliosError):
    """Input does not satisfy the HERMES-compatible market-fact contract.

    Raised for malformed candles, malformed frames, malformed windows and
    malformed identity/value tokens.
    """


class FreshnessError(HeliosError):
    """Base class for freshness/validity failures."""


class StaleFactError(FreshnessError):
    """A required market fact exists but is older than its configured limit."""


class MissingFactError(FreshnessError):
    """A required market fact, indicator or window is absent."""


class IllegalStateTransitionError(HeliosError):
    """A proposed strategy-state transition is not in the documented legal set."""


class IdentityError(HeliosError):
    """A strategy/chain identity or version is malformed, or immutability was violated."""


class StrategySpecError(HeliosError):
    """A strategy package/specification is malformed, ambiguous or incomplete.

    HELIOS never invents strategy logic to fill a gap in a specification. Any
    ambiguity is returned to HSA as this error.
    """


class ConfigurationError(HeliosError):
    """Required runtime configuration is missing or invalid."""


class SerialisationError(HeliosError):
    """A value could not be deterministically serialised or was rejected on read."""
