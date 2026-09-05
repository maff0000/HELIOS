"""The interfaces later work items implement.

WI-1 defines the vocabulary; it deliberately implements no trading logic. What
belongs here is the shape of the contract between the engine and a strategy:
what a strategy is handed, and what it must return.

An evaluator receives an immutable :class:`EvaluationContext` and returns a
:class:`~helios.contracts.output.StrategyStateEnvelope`. It is handed no
account, no downstream system and no clock of its own — everything it may read
is on the context, which is why HELIOS is execution-blind by construction
rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from helios.clock import ensure_utc
from helios.contracts._tokens import Instrument, SemanticRole
from helios.contracts.freshness import FreshnessPolicy
from helios.contracts.identity import StrategyIdentity
from helios.contracts.output import StrategyStateEnvelope
from helios.contracts.timeframe import Timeframe
from helios.contracts.window import MarketFactWindow
from helios.errors import MissingFactError


@dataclass(frozen=True, slots=True)
class RequiredInput:
    """One market-fact input a strategy declares it needs."""

    role: SemanticRole
    timeframe: Timeframe
    lookback: int
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """Everything a strategy is permitted to see during one evaluation.

    Deliberately absent: any downstream execution state. There is no field on
    this object through which a strategy could learn whether anything acted on
    its output, which is what makes identical inputs produce identical state.
    """

    instrument: Instrument
    evaluated_at_utc: datetime
    windows: Mapping[SemanticRole, MarketFactWindow]
    parameters: Mapping[str, Decimal | int | str | bool]
    freshness_policy: FreshnessPolicy
    previous: Optional[StrategyStateEnvelope] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evaluated_at_utc", ensure_utc(self.evaluated_at_utc, field="evaluated_at_utc")
        )
        object.__setattr__(self, "windows", MappingProxyType(dict(self.windows)))
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    def window_for(self, role: SemanticRole | str) -> MarketFactWindow:
        """The window bound to a semantic role, failing loudly if unbound.

        Which timeframe fills a role is decided by the strategy package, never
        by HELIOS.
        """
        key = role if isinstance(role, SemanticRole) else SemanticRole(str(role))
        window = self.windows.get(key)
        if window is None:
            raise MissingFactError(
                "no market-fact window is bound to this semantic role",
                role=str(key),
                bound_roles=sorted(str(name) for name in self.windows),
            )
        return window

    def parameter(self, name: str) -> Any:
        """A declared strategy parameter, failing loudly if it was not declared."""
        if name not in self.parameters:
            raise MissingFactError(
                "strategy parameter was not supplied by the strategy package",
                parameter=name,
                declared=sorted(self.parameters),
            )
        return self.parameters[name]


@runtime_checkable
class StrategyEvaluator(Protocol):
    """What every atomic strategy and every chain implements.

    Implementations must be pure with respect to the context: same context in,
    same envelope out, no hidden state, no I/O.
    """

    @property
    def identity(self) -> StrategyIdentity:
        """The immutable identity/version this evaluator publishes under."""
        ...

    def required_inputs(self) -> tuple[RequiredInput, ...]:
        """The market facts this strategy declares it needs."""
        ...

    def evaluate(self, context: EvaluationContext) -> StrategyStateEnvelope:
        """Produce the normalised state for one evaluation."""
        ...
