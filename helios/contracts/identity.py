"""Strategy and chain identity — CER-compatible.

HELIOS *originates* two canonical identity fields, ``strategy_id`` and
``strategy_version``, and carries them through every published envelope. The
remaining CER identity fields (``experiment_id``, ``run_id``, ``evidence_id``,
``artifact_id``) are owned and assigned by CER. HELIOS neither mints nor
stores them: HELIOS is not an evidence store, and a guard test asserts those
field names never appear in the HELIOS output envelope.

Immutability semantics
----------------------
A promoted ``(strategy_id, strategy_version)`` pair is immutable. A change in
strategy logic is a NEW version, never an in-place edit of an existing one.
The types below are frozen at the object level, and
:func:`assert_version_immutable` expresses the promotion rule so that callers
which hold a promoted identity fail loudly if they try to redefine it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic_core import core_schema

from helios.errors import IdentityError
from helios.contracts._tokens import Token

#: Every canonical CER identity field, in CER's own vocabulary.
CER_IDENTITY_FIELDS: tuple[str, ...] = (
    "strategy_id",
    "strategy_version",
    "experiment_id",
    "run_id",
    "evidence_id",
    "artifact_id",
)

#: The subset HELIOS originates and publishes.
HELIOS_ORIGINATED_IDENTITY_FIELDS: tuple[str, ...] = ("strategy_id", "strategy_version")

#: The subset CER owns. HELIOS must never mint, store or publish these; doing
#: so would make HELIOS a competing evidence repository.
CER_OWNED_IDENTITY_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "run_id",
    "evidence_id",
    "artifact_id",
)


class StrategyId(Token):
    """Stable identifier of one atomic strategy, e.g. ``golden_cross``."""

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
    LABEL: ClassVar[str] = "strategy_id"
    ERROR: ClassVar[type] = IdentityError


class ChainId(Token):
    """Stable identifier of one strategy chain, e.g. ``gold_context_trigger``.

    Structurally distinct from :class:`StrategyId` so a chain identifier
    cannot be silently used where an atomic strategy is required.
    """

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
    LABEL: ClassVar[str] = "chain_id"
    ERROR: ClassVar[type] = IdentityError


_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True, order=True)
class StrategyVersion:
    """An immutable ``major.minor.patch`` strategy version.

    Leading zeros and pre-release suffixes are rejected: a version must be
    unambiguous, because it is the key CER records empirical evidence against.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        for name, part in (("major", self.major), ("minor", self.minor), ("patch", self.patch)):
            if isinstance(part, bool) or not isinstance(part, int):
                raise IdentityError(
                    "malformed strategy_version component: expected an integer",
                    component=name,
                    received_type=type(part).__name__,
                )
            if part < 0:
                raise IdentityError(
                    "malformed strategy_version component: must not be negative",
                    component=name,
                    value=part,
                )

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, value: Any) -> "StrategyVersion":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise IdentityError(
                "malformed strategy_version: expected a 'major.minor.patch' string",
                received_type=type(value).__name__,
            )
        match = _VERSION_PATTERN.fullmatch(value.strip())
        if match is None:
            raise IdentityError(
                "malformed strategy_version",
                value=value,
                expected_pattern=_VERSION_PATTERN.pattern,
            )
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def next_major(self) -> "StrategyVersion":
        """A new version for a breaking logic change (never an in-place edit)."""
        return StrategyVersion(self.major + 1, 0, 0)

    def next_minor(self) -> "StrategyVersion":
        return StrategyVersion(self.major, self.minor + 1, 0)

    def next_patch(self) -> "StrategyVersion":
        return StrategyVersion(self.major, self.minor, self.patch + 1)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        return core_schema.no_info_plain_validator_function(
            cls.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema(), when_used="always"
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> Any:
        return {
            "type": "string",
            "pattern": _VERSION_PATTERN.pattern,
            "title": "StrategyVersion",
        }


@dataclass(frozen=True, slots=True)
class StrategyIdentity:
    """The immutable ``(strategy_id, strategy_version)`` pair CER keys against."""

    strategy_id: StrategyId
    strategy_version: StrategyVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", StrategyId._coerce(self.strategy_id))
        object.__setattr__(
            self, "strategy_version", StrategyVersion.parse(self.strategy_version)
        )

    @property
    def canonical(self) -> str:
        """``golden_cross@1.0.0`` — the canonical single-string form."""
        return f"{self.strategy_id}@{self.strategy_version}"

    def __str__(self) -> str:
        return self.canonical

    @classmethod
    def parse(cls, value: Any) -> "StrategyIdentity":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str) or value.count("@") != 1:
            raise IdentityError(
                "malformed strategy identity: expected 'strategy_id@major.minor.patch'",
                value=value if isinstance(value, str) else type(value).__name__,
            )
        raw_id, raw_version = value.split("@")
        return cls(StrategyId(raw_id), StrategyVersion.parse(raw_version))


@dataclass(frozen=True, slots=True)
class ChainIdentity:
    """The immutable ``(chain_id, chain_version)`` pair for a composite."""

    chain_id: ChainId
    chain_version: StrategyVersion

    def __post_init__(self) -> None:
        object.__setattr__(self, "chain_id", ChainId._coerce(self.chain_id))
        object.__setattr__(self, "chain_version", StrategyVersion.parse(self.chain_version))

    @property
    def canonical(self) -> str:
        return f"{self.chain_id}@{self.chain_version}"

    def __str__(self) -> str:
        return self.canonical


def assert_version_immutable(
    promoted: StrategyIdentity, proposed: StrategyIdentity, *, definition_changed: bool
) -> None:
    """Enforce the promotion rule: a promoted version is never redefined.

    ``definition_changed`` is the caller's assertion that strategy logic or
    parameters differ from what was promoted. If the definition changed, the
    version MUST change too; otherwise HELIOS raises rather than allowing an
    in-place tweak that would silently invalidate CER's recorded evidence.
    """
    if promoted.strategy_id != proposed.strategy_id:
        raise IdentityError(
            "strategy_id mismatch: a different strategy cannot reuse this identity",
            promoted=promoted.canonical,
            proposed=proposed.canonical,
        )
    if definition_changed and promoted.strategy_version == proposed.strategy_version:
        raise IdentityError(
            "promoted strategy_version is immutable: a logic change requires a NEW version",
            promoted=promoted.canonical,
            proposed=proposed.canonical,
        )
    if not definition_changed and promoted.strategy_version != proposed.strategy_version:
        raise IdentityError(
            "strategy_version changed without a definition change",
            promoted=promoted.canonical,
            proposed=proposed.canonical,
        )
