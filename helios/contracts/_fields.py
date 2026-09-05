"""Shared field primitives and the frozen model base.

Two deliberate strictnesses live here:

* **Binary floats are rejected at the contract boundary.** ``0.1 + 0.2`` is not
  ``0.3`` in binary floating point, and HELIOS promises that identical ordered
  inputs produce identical output. Prices, indicator values and thresholds are
  :class:`~decimal.Decimal`, fed from exact decimal text. Callers holding a
  float must state their intent by converting it themselves.
* **Naive datetimes are rejected.** See :mod:`helios.clock`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, ValidationError

from helios.clock import ensure_utc, from_iso8601_utc
from helios.errors import ContractViolationError


class FrozenMapping(dict):
    """A dict that refuses mutation.

    Determinism and isolation require that a published envelope cannot be
    edited after the fact by whoever holds it. Frozen models already block
    field reassignment; this blocks mutation of the one mapping field.
    It subclasses ``dict`` so serialisation and JSON Schema stay ordinary.
    """

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise ContractViolationError(
            "this mapping is immutable; build a new value instead"
        )

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    pop = _immutable
    popitem = _immutable
    clear = _immutable
    update = _immutable
    setdefault = _immutable

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(tuple(sorted(self.items(), key=lambda item: item[0])))

    def __copy__(self) -> "FrozenMapping":
        return self

    def __deepcopy__(self, memo: Any) -> "FrozenMapping":
        return self


def freeze_mapping(value: Any) -> FrozenMapping:
    """Wrap a validated mapping so it cannot be mutated afterwards."""
    return value if isinstance(value, FrozenMapping) else FrozenMapping(value)


def to_decimal(value: Any, *, field: str = "value") -> Decimal:
    """Coerce to an exact :class:`Decimal`, rejecting anything lossy or non-finite."""
    if isinstance(value, bool):
        raise ContractViolationError(
            "expected a decimal number, received a boolean", field=field
        )
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ContractViolationError(
                "malformed decimal literal", field=field, value=value
            ) from exc
    elif isinstance(value, float):
        raise ContractViolationError(
            "binary float rejected: supply an exact decimal string or Decimal "
            "so that evaluation stays deterministic",
            field=field,
            value=repr(value),
        )
    else:
        raise ContractViolationError(
            "expected a decimal number", field=field, received_type=type(value).__name__
        )
    if not candidate.is_finite():
        raise ContractViolationError(
            "non-finite decimal rejected", field=field, value=str(candidate)
        )
    return candidate


def _to_utc(value: Any) -> datetime:
    if isinstance(value, str):
        return from_iso8601_utc(value)
    return ensure_utc(value)


#: An exact decimal quantity.
DecimalValue = Annotated[Decimal, BeforeValidator(to_decimal)]

#: A timezone-aware UTC instant, parsed from ISO-8601 text or a datetime.
UtcDatetime = Annotated[datetime, BeforeValidator(_to_utc)]


def summarise_validation_error(exc: ValidationError) -> list[dict[str, Any]]:
    """Reduce a pydantic error to a compact, log-safe, deterministic summary."""
    summary: list[dict[str, Any]] = []
    for error in exc.errors():
        summary.append(
            {
                "field": ".".join(str(part) for part in error.get("loc", ())) or "<root>",
                "problem": error.get("msg", ""),
                "type": error.get("type", ""),
            }
        )
    return sorted(summary, key=lambda item: (item["field"], item["type"]))


class HeliosModel(BaseModel):
    """Frozen, closed-world base model.

    ``frozen=True`` — one strategy structurally cannot mutate a fact or an
    envelope another strategy holds.

    ``extra="forbid"`` — an unrecognised field is a loud failure, not a silent
    pass-through. If HERMES adds a field, that is a HELIOS schema-version
    change made deliberately, never an accident.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_default=True,
        arbitrary_types_allowed=False,
    )

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise ContractViolationError(
                f"malformed {type(self).__name__}",
                problems=summarise_validation_error(exc),
            ) from exc


def validate_model(model_type: type[BaseModel], data: Any, *, label: str | None = None) -> Any:
    """Validate untrusted data into a model, raising the HELIOS error taxonomy."""
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise ContractViolationError(
            f"malformed {label or model_type.__name__}",
            problems=summarise_validation_error(exc),
        ) from exc
