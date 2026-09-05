"""Frozen, validated token value types.

These give HELIOS *structural* type safety: a :class:`StrategyId` cannot be
passed where an :class:`Instrument` is expected, even though both are strings
at rest. Every token validates on construction and is immutable thereafter.

Each token integrates with pydantic so that contract models accept and emit
plain scalars (``"XAU_USD"``), not nested objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic_core import core_schema

from helios.errors import ContractViolationError


@dataclass(frozen=True, slots=True, order=True)
class Token:
    """Base class for a validated, immutable string token."""

    value: str

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_]{1,64}$")
    LABEL: ClassVar[str] = "token"
    #: Which error a malformed value raises. Identity tokens raise the identity
    #: error so the taxonomy matches the concept, not the implementation.
    ERROR: ClassVar[type[ContractViolationError]] = ContractViolationError

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise self.ERROR(
                f"malformed {self.LABEL}: expected a string",
                token_type=type(self).__name__,
                received_type=type(self.value).__name__,
            )
        if not self.PATTERN.fullmatch(self.value):
            raise self.ERROR(
                f"malformed {self.LABEL}",
                token_type=type(self).__name__,
                value=self.value,
                expected_pattern=self.PATTERN.pattern,
            )

    def __str__(self) -> str:
        return self.value

    @classmethod
    def _coerce(cls, value: Any) -> "Token":
        if isinstance(value, cls):
            return value
        if isinstance(value, Token):
            raise cls.ERROR(
                f"cannot use a {type(value).__name__} where a {cls.__name__} is required",
                value=value.value,
            )
        if isinstance(value, str):
            return cls(value)
        raise cls.ERROR(
            f"malformed {cls.LABEL}: expected a string",
            token_type=cls.__name__,
            received_type=type(value).__name__,
        )

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        return core_schema.no_info_plain_validator_function(
            cls._coerce,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda token: token.value,
                return_schema=core_schema.str_schema(),
                when_used="always",
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> Any:
        return {"type": "string", "pattern": cls.PATTERN.pattern, "title": cls.__name__}


class Instrument(Token):
    """A tradeable instrument symbol as HERMES names it, e.g. ``XAU_USD``."""

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Z0-9]{2,10}(_[A-Z0-9]{2,10})?$")
    LABEL: ClassVar[str] = "instrument symbol"


class SemanticRole(Token):
    """The role a timeframe plays *within one strategy definition*.

    Roles are an open vocabulary on purpose. The PID's GOLD template uses
    CONTEXT / LOCATION / CONFIRMATION / TRIGGER, but strategies may use fewer
    or more stages. Binding a role to a timeframe is *per-strategy
    configuration* declared in the strategy package — there is deliberately no
    global role-to-timeframe constant anywhere in HELIOS.
    """

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
    LABEL: ClassVar[str] = "semantic role"


class Regime(Token):
    """A market regime label as reported by HERMES.

    HERMES owns this vocabulary, so the token is validated for *shape* but the
    value set is open: an unrecognised-but-well-formed regime is a fact HELIOS
    does not understand, not malformed input. Strategies that depend on a
    specific regime compare against it explicitly.
    """

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
    LABEL: ClassVar[str] = "regime"


class Session(Token):
    """A market session label as reported by HERMES (open vocabulary)."""

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
    LABEL: ClassVar[str] = "session"


class SourceName(Token):
    """The name of a fact source/feed, e.g. ``hermes``."""

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")
    LABEL: ClassVar[str] = "source name"


# Regime values observed on the real HERMES feed. Documentation, not a closed
# set: HELIOS accepts any well-formed regime token.
REGIME_BULL_TREND = Regime("BULL_TREND")
REGIME_BEAR_TREND = Regime("BEAR_TREND")
REGIME_LOW_VOLATILITY = Regime("LOW_VOLATILITY")
REGIME_TRANSITION = Regime("TRANSITION")

KNOWN_REGIMES: frozenset[Regime] = frozenset(
    {REGIME_BULL_TREND, REGIME_BEAR_TREND, REGIME_LOW_VOLATILITY, REGIME_TRANSITION}
)

# The PID's GOLD strategy-engineering template roles. Again: names only. Which
# timeframe fills which role is declared per strategy, never here.
ROLE_CONTEXT = SemanticRole("CONTEXT")
ROLE_LOCATION = SemanticRole("LOCATION")
ROLE_CONFIRMATION = SemanticRole("CONFIRMATION")
ROLE_TRIGGER = SemanticRole("TRIGGER")

TEMPLATE_ROLES: tuple[SemanticRole, ...] = (
    ROLE_CONTEXT,
    ROLE_LOCATION,
    ROLE_CONFIRMATION,
    ROLE_TRIGGER,
)
