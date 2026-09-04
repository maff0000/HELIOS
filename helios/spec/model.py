"""The HSA-targetable strategy package format.

HSA is the strategy-engineering authority. It emits declarative strategy
packages; HELIOS loads them. The format below is the contract between those
two systems.

The governing rule is that HELIOS **never invents trading logic to fill a gap**.
Every ambiguity is therefore a loud failure rather than a default:

* an unrecognised ``schema_version`` is refused, not guessed at;
* an unknown key is refused (typos do not become silent no-ops);
* an ``ALL`` chain that also declares an ordering is refused, because that is
  two contradictory statements about the same thing;
* a ``SEQUENCE`` chain without an ordering window is refused, because
  "in order, within what?" has no safe default;
* an expiry mode without its accompanying value is refused.

Note also what is NOT here: no global role-to-timeframe constant. A package
declares which timeframe fills which semantic role for itself, which is what
lets one strategy use 4H CONTEXT and another use D1 CONTEXT.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Optional, Union

from pydantic import AfterValidator, field_validator, model_validator

from helios.contracts._fields import (
    DecimalValue,
    HeliosModel,
    UtcDatetime,
    freeze_mapping,
)
from helios.contracts._tokens import SemanticRole
from helios.contracts.identity import StrategyId, StrategyVersion
from helios.contracts.market_fact import FACT_FIELDS
from helios.contracts.state import Direction, StrategyState, _CodedEnum
from helios.contracts.timeframe import Timeframe
from helios.errors import StrategySpecError

#: The package schema version HELIOS understands. A package declaring anything
#: else is refused.
STRATEGY_PACKAGE_SCHEMA_VERSION = "helios.strategy_package/1.0.0"


class PackageKind(_CodedEnum):
    ATOMIC = "ATOMIC"
    CHAIN = "CHAIN"


class ChainPrimitive(_CodedEnum):
    """The canonical v1 composition primitives."""

    ALL = "ALL"
    ANY = "ANY"
    SEQUENCE = "SEQUENCE"
    CONTEXT_TRIGGER = "CONTEXT_TRIGGER"


class EvaluateOn(_CodedEnum):
    """Which frames the strategy is evaluated against."""

    CLOSED_FRAME = "CLOSED_FRAME"
    EVERY_FRAME = "EVERY_FRAME"


class ExpiryMode(_CodedEnum):
    NEVER = "NEVER"
    FRAMES = "FRAMES"
    DURATION = "DURATION"


class DirectionMode(_CodedEnum):
    DIRECTIONAL = "DIRECTIONAL"
    NON_DIRECTIONAL = "NON_DIRECTIONAL"


class DirectionResolution(_CodedEnum):
    """Where the published direction comes from."""

    STRATEGY_LOCAL = "STRATEGY_LOCAL"
    FROM_COMPONENTS = "FROM_COMPONENTS"


class DirectionRelationship(_CodedEnum):
    """How a component's direction must relate to the chain's."""

    SAME = "SAME"
    OPPOSITE = "OPPOSITE"
    ANY = "ANY"


class ParameterType(_CodedEnum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    STRING = "STRING"


class PackageIdentity(HeliosModel):
    """CER-compatible identity of the packaged strategy or chain."""

    strategy_id: StrategyId
    strategy_version: StrategyVersion


class PackageMetadata(HeliosModel):
    """Human-facing provenance of the package itself."""

    title: str
    description: str
    authored_by: str
    authored_at_utc: UtcDatetime

    @field_validator("title", "description", "authored_by")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise StrategySpecError("package metadata fields must not be blank")
        return value


class InputRequirement(HeliosModel):
    """One HERMES input the strategy declares it needs.

    This is the ONLY place a semantic role is bound to a timeframe, and it is
    per package by design.
    """

    role: SemanticRole
    timeframe: Timeframe
    lookback: int
    required_fields: tuple[str, ...]
    max_age_seconds: Optional[int] = None

    @field_validator("lookback")
    @classmethod
    def _lookback_is_positive(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise StrategySpecError("input lookback must be a positive integer",
                                    value=repr(value))
        return value

    @field_validator("max_age_seconds")
    @classmethod
    def _max_age_is_positive(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and (isinstance(value, bool) or value < 1):
            raise StrategySpecError("max_age_seconds must be a positive integer",
                                    value=repr(value))
        return value

    @field_validator("required_fields")
    @classmethod
    def _fields_are_known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise StrategySpecError(
                "an input must declare at least one required market-fact field"
            )
        unknown = [name for name in value if name not in FACT_FIELDS]
        if unknown:
            raise StrategySpecError(
                "input declares market-fact fields HELIOS does not know; "
                "HELIOS will not invent a fact HERMES does not publish",
                unknown=sorted(unknown),
                known=list(FACT_FIELDS),
            )
        if len(set(value)) != len(value):
            raise StrategySpecError(
                "input declares a duplicated required field", fields=list(value)
            )
        return value


class ParameterSpec(HeliosModel):
    """A declared parameter with its value and its permitted range."""

    type: ParameterType
    value: Union[bool, int, DecimalValue, str]
    minimum: Optional[DecimalValue] = None
    maximum: Optional[DecimalValue] = None
    allowed: Optional[tuple[str, ...]] = None
    description: Optional[str] = None

    @model_validator(mode="after")
    def _value_matches_its_declaration(self) -> "ParameterSpec":
        declared = self.type
        value = self.value
        if declared is ParameterType.BOOLEAN:
            if not isinstance(value, bool):
                raise StrategySpecError("parameter declared BOOLEAN but value is not",
                                        value=repr(value))
        elif declared is ParameterType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise StrategySpecError("parameter declared INTEGER but value is not",
                                        value=repr(value))
        elif declared is ParameterType.DECIMAL:
            if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                raise StrategySpecError("parameter declared DECIMAL but value is not",
                                        value=repr(value))
        else:
            if not isinstance(value, str):
                raise StrategySpecError("parameter declared STRING but value is not",
                                        value=repr(value))
        numeric = declared in (ParameterType.INTEGER, ParameterType.DECIMAL)
        if not numeric and (self.minimum is not None or self.maximum is not None):
            raise StrategySpecError(
                "minimum/maximum are meaningless for a non-numeric parameter",
                type=declared.value,
            )
        if declared is not ParameterType.STRING and self.allowed is not None:
            raise StrategySpecError(
                "'allowed' is meaningful only for a STRING parameter", type=declared.value
            )
        if self.allowed is not None:
            if not self.allowed:
                raise StrategySpecError("'allowed' must list at least one value")
            if value not in self.allowed:
                raise StrategySpecError(
                    "parameter value is not in its allowed set",
                    value=repr(value),
                    allowed=list(self.allowed),
                )
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise StrategySpecError(
                "parameter minimum exceeds maximum",
                minimum=str(self.minimum),
                maximum=str(self.maximum),
            )
        if numeric:
            as_decimal = Decimal(value) if isinstance(value, int) else value
            if self.minimum is not None and as_decimal < self.minimum:
                raise StrategySpecError(
                    "parameter value is below its declared minimum",
                    value=str(as_decimal),
                    minimum=str(self.minimum),
                )
            if self.maximum is not None and as_decimal > self.maximum:
                raise StrategySpecError(
                    "parameter value is above its declared maximum",
                    value=str(as_decimal),
                    maximum=str(self.maximum),
                )
        return self


class DirectionSpec(HeliosModel):
    """How the package expresses direction."""

    mode: DirectionMode
    resolution: DirectionResolution
    component_relationship: Optional[DirectionRelationship] = None

    @model_validator(mode="after")
    def _relationship_is_coherent(self) -> "DirectionSpec":
        if self.resolution is DirectionResolution.FROM_COMPONENTS:
            if self.component_relationship is None:
                raise StrategySpecError(
                    "a chain resolving direction from components must state how "
                    "component directions relate (SAME, OPPOSITE or ANY)"
                )
            if (
                self.mode is DirectionMode.NON_DIRECTIONAL
                and self.component_relationship is not DirectionRelationship.ANY
            ):
                raise StrategySpecError(
                    "a NON_DIRECTIONAL package cannot constrain component directions",
                    component_relationship=self.component_relationship.value,
                )
        elif self.component_relationship is not None:
            raise StrategySpecError(
                "component_relationship is meaningful only when direction is "
                "resolved from components"
            )
        return self


class TimingSpec(HeliosModel):
    """When the strategy is evaluated."""

    evaluate_on: EvaluateOn


class PersistenceSpec(HeliosModel):
    """How long a match must hold, and whether weakening is published."""

    min_matched_frames: int
    weakening_enabled: bool

    @field_validator("min_matched_frames")
    @classmethod
    def _at_least_one(cls, value: int) -> int:
        if isinstance(value, bool) or value < 1:
            raise StrategySpecError(
                "min_matched_frames must be a positive integer", value=repr(value)
            )
        return value


class ExpirySpec(HeliosModel):
    """When an unresolved match ages out."""

    mode: ExpiryMode
    frames: Optional[int] = None
    duration_seconds: Optional[int] = None

    @model_validator(mode="after")
    def _mode_matches_its_value(self) -> "ExpirySpec":
        if self.mode is ExpiryMode.FRAMES:
            if self.frames is None:
                raise StrategySpecError("expiry mode FRAMES requires 'frames'")
            if isinstance(self.frames, bool) or self.frames < 1:
                raise StrategySpecError("expiry 'frames' must be a positive integer",
                                        value=repr(self.frames))
            if self.duration_seconds is not None:
                raise StrategySpecError(
                    "expiry declares both a frame count and a duration; "
                    "HELIOS will not choose between them"
                )
        elif self.mode is ExpiryMode.DURATION:
            if self.duration_seconds is None:
                raise StrategySpecError("expiry mode DURATION requires 'duration_seconds'")
            if isinstance(self.duration_seconds, bool) or self.duration_seconds < 1:
                raise StrategySpecError(
                    "expiry 'duration_seconds' must be a positive integer",
                    value=repr(self.duration_seconds),
                )
            if self.frames is not None:
                raise StrategySpecError(
                    "expiry declares both a duration and a frame count; "
                    "HELIOS will not choose between them"
                )
        else:
            if self.frames is not None or self.duration_seconds is not None:
                raise StrategySpecError(
                    "expiry mode NEVER must not carry a frame count or duration"
                )
        return self


class ChainComponent(HeliosModel):
    """One atomic strategy participating in a chain.

    Components reference atomic strategies. v1 chains consume atoms directly;
    chain-of-chain recursion is out of scope without explicit architecture
    authority, and is refused by the loader.
    """

    strategy_id: StrategyId
    strategy_version: StrategyVersion
    role: Optional[SemanticRole] = None
    sequence_index: Optional[int] = None
    direction_relationship: DirectionRelationship
    required_states: tuple[StrategyState, ...]

    @field_validator("required_states")
    @classmethod
    def _states_are_stated(cls, value: tuple[StrategyState, ...]) -> tuple[StrategyState, ...]:
        if not value:
            raise StrategySpecError(
                "a chain component must state which component states satisfy it"
            )
        if len(set(value)) != len(value):
            raise StrategySpecError(
                "chain component repeats a required state",
                states=[state.value for state in value],
            )
        return value

    @field_validator("sequence_index")
    @classmethod
    def _index_is_not_negative(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and (isinstance(value, bool) or value < 0):
            raise StrategySpecError(
                "sequence_index must be a non-negative integer", value=repr(value)
            )
        return value


class ChainSpec(HeliosModel):
    """The composition definition."""

    primitive: ChainPrimitive
    components: tuple[ChainComponent, ...]
    ordering_window_seconds: Optional[int] = None
    explanation_required: bool

    @model_validator(mode="after")
    def _primitive_rules_hold(self) -> "ChainSpec":
        components = self.components
        if len(components) < 2:
            raise StrategySpecError(
                "a chain must combine at least two components",
                primitive=self.primitive.value,
                component_count=len(components),
            )
        identifiers = [str(component.strategy_id) for component in components]
        if len(set(identifiers)) != len(identifiers):
            raise StrategySpecError(
                "a chain lists the same component strategy twice; the chain's "
                "intent would be ambiguous",
                components=sorted(identifiers),
            )
        indexed = [component for component in components if component.sequence_index is not None]
        if self.primitive is ChainPrimitive.SEQUENCE:
            if len(indexed) != len(components):
                raise StrategySpecError(
                    "every component of a SEQUENCE chain must declare a sequence_index"
                )
            indices = sorted(component.sequence_index for component in components)  # type: ignore[misc]
            if indices != list(range(len(components))):
                raise StrategySpecError(
                    "SEQUENCE component indices must be unique and contiguous from 0",
                    indices=indices,
                )
            if self.ordering_window_seconds is None:
                raise StrategySpecError(
                    "a SEQUENCE chain must declare ordering_window_seconds; "
                    "'in order, within what period?' has no safe default"
                )
        else:
            if indexed:
                raise StrategySpecError(
                    "only a SEQUENCE chain may declare sequence_index",
                    primitive=self.primitive.value,
                )
            if self.ordering_window_seconds is not None:
                raise StrategySpecError(
                    "ordering_window_seconds is meaningful only for a SEQUENCE chain",
                    primitive=self.primitive.value,
                )
        if self.ordering_window_seconds is not None and (
            isinstance(self.ordering_window_seconds, bool) or self.ordering_window_seconds < 1
        ):
            raise StrategySpecError(
                "ordering_window_seconds must be a positive integer",
                value=repr(self.ordering_window_seconds),
            )
        if self.primitive is ChainPrimitive.CONTEXT_TRIGGER:
            roles = [component.role for component in components]
            if any(role is None for role in roles):
                raise StrategySpecError(
                    "every component of a CONTEXT_TRIGGER chain must declare its role"
                )
            role_names = [str(role) for role in roles]
            for required in ("CONTEXT", "TRIGGER"):
                if role_names.count(required) != 1:
                    raise StrategySpecError(
                        "a CONTEXT_TRIGGER chain requires exactly one component in "
                        "each of the CONTEXT and TRIGGER roles",
                        role=required,
                        found=role_names.count(required),
                        roles=role_names,
                    )
            if len(set(role_names)) != len(role_names):
                raise StrategySpecError(
                    "a CONTEXT_TRIGGER chain assigns the same role twice", roles=role_names
                )
        return self


ParameterMap = Annotated[dict[str, ParameterSpec], AfterValidator(freeze_mapping)]


class StrategyPackage(HeliosModel):
    """A complete, machine-consumable strategy or chain definition."""

    schema_version: str
    kind: PackageKind
    identity: PackageIdentity
    metadata: PackageMetadata
    inputs: tuple[InputRequirement, ...]
    parameters: ParameterMap
    direction: DirectionSpec
    timing: TimingSpec
    persistence: PersistenceSpec
    expiry: ExpirySpec
    chain: Optional[ChainSpec] = None

    @field_validator("schema_version")
    @classmethod
    def _schema_version_is_known(cls, value: str) -> str:
        if value != STRATEGY_PACKAGE_SCHEMA_VERSION:
            raise StrategySpecError(
                "unknown strategy package schema_version; HELIOS refuses to guess "
                "what an unfamiliar package format means",
                received=value,
                supported=STRATEGY_PACKAGE_SCHEMA_VERSION,
            )
        return value

    @model_validator(mode="after")
    def _package_is_coherent(self) -> "StrategyPackage":
        roles = [str(requirement.role) for requirement in self.inputs]
        if len(set(roles)) != len(roles):
            raise StrategySpecError(
                "a semantic role is bound to more than one input; the role-to-"
                "timeframe mapping would be ambiguous",
                roles=sorted(roles),
            )
        if self.kind is PackageKind.ATOMIC:
            if self.chain is not None:
                raise StrategySpecError(
                    "an ATOMIC package must not declare a chain",
                    strategy_id=str(self.identity.strategy_id),
                )
            if not self.inputs:
                raise StrategySpecError(
                    "an ATOMIC package must declare the HERMES inputs it needs",
                    strategy_id=str(self.identity.strategy_id),
                )
            if self.direction.resolution is not DirectionResolution.STRATEGY_LOCAL:
                raise StrategySpecError(
                    "an ATOMIC package resolves direction locally; it has no components",
                    resolution=self.direction.resolution.value,
                )
        else:
            if self.chain is None:
                raise StrategySpecError(
                    "a CHAIN package must declare its chain definition",
                    strategy_id=str(self.identity.strategy_id),
                )
            if self.direction.resolution is not DirectionResolution.FROM_COMPONENTS:
                raise StrategySpecError(
                    "a CHAIN package resolves direction from its components",
                    resolution=self.direction.resolution.value,
                )
            if self.inputs:
                declared = set(roles)
                for component in self.chain.components:
                    if component.role is not None and str(component.role) not in declared:
                        raise StrategySpecError(
                            "a chain component plays a role the package did not declare "
                            "as an input",
                            role=str(component.role),
                            declared_roles=sorted(declared),
                        )
        return self

    @property
    def role_timeframes(self) -> dict[str, Timeframe]:
        """This package's own semantic role -> timeframe mapping."""
        return {str(item.role): item.timeframe for item in self.inputs}

    @property
    def parameter_values(self) -> dict[str, Any]:
        """Declared parameter values, ready to hand to an evaluation context."""
        return {name: spec.value for name, spec in self.parameters.items()}
