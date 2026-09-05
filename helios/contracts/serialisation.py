"""Deterministic canonical JSON.

FALCON consumes HELIOS output. That output must be stable and
self-describing: the same state must serialise to the same bytes on every
process, on every host, on every run. This module is the single place that
decides how a HELIOS value becomes JSON.

Rules:

* object keys are emitted in sorted order;
* no insignificant whitespace;
* instants are fixed-width ISO-8601 UTC ending in ``Z``;
* decimals are emitted as normalised strings, never as binary floats, so no
  precision is lost and no platform-specific float repr can leak in. The
  normalisation is arithmetic-free and reads only the value's own sign, digits
  and exponent, so the emitted digits cannot be changed by whatever decimal
  context happens to be installed in the process;
* ``NaN``/``Infinity`` are rejected rather than emitted as invalid JSON;
* ``None`` is preserved as ``null`` — an absent fact is published as absent,
  not omitted, so consumers never have to guess whether a field was dropped.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from helios.clock import to_iso8601_utc
from helios.contracts._tokens import Token
from helios.errors import SerialisationError


def canonical_decimal(value: Decimal) -> str:
    """Normalised, exact decimal text: ``2400.00`` -> ``"2400"``, ``1.50`` -> ``"1.5"``.

    Deliberately arithmetic-free. ``Decimal.normalize()`` is an *operation*, so
    it is rounded by whatever decimal context the process has installed: under
    a ``prec=4`` context it would emit ``0.8462`` where the default context
    emits ``0.846154``, and the published bytes would then depend on ambient
    state HELIOS does not control. Any library or host code installing its own
    context would silently change what FALCON receives.

    This reads the value's own ``(sign, digits, exponent)`` triple instead,
    strips the trailing zeros of the fractional part, and rebuilds the decimal
    exactly — construction from a tuple applies no context. The emitted digits
    therefore depend only on the value.
    """
    if not value.is_finite():
        raise SerialisationError("cannot serialise a non-finite decimal", value=str(value))
    sign, digits, exponent = value.as_tuple()
    if not any(digits):
        # Every zero publishes as "0": 0, 0.00 and -0 are one value here.
        return "0"
    kept = list(digits)
    # Only fractional trailing zeros are insignificant. 2400 keeps its zeros
    # because they are integer places, not trailing precision.
    while exponent < 0 and kept[-1] == 0:
        kept.pop()
        exponent += 1
    # ``format(..., "f")`` with no precision neither rounds nor consults the
    # context; it lays out the exact digits in positional notation.
    return format(Decimal((sign, tuple(kept), exponent)), "f")


def canonicalise(value: Any) -> Any:
    """Reduce a HELIOS value to JSON-native types, deterministically."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, float):
        raise SerialisationError(
            "binary float cannot be serialised deterministically; use Decimal",
            value=repr(value),
        )
    if isinstance(value, datetime):
        return to_iso8601_utc(value)
    if isinstance(value, timedelta):
        raise SerialisationError(
            "durations must be published as an explicit integer '*_seconds' field",
            value=repr(value),
        )
    if isinstance(value, Token):
        return value.value
    if isinstance(value, Enum):
        member_value = value.value
        code = getattr(value, "code", None)
        return code if isinstance(code, str) else canonicalise(member_value)
    if isinstance(value, BaseModel):
        return canonicalise(value.model_dump(mode="python"))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, Token):
                key = key.value
            elif isinstance(key, Enum):
                key = getattr(key, "code", None) or str(key.value)
            if not isinstance(key, str):
                raise SerialisationError(
                    "object keys must be strings", key_type=type(key).__name__
                )
            result[key] = canonicalise(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    ):
        return [canonicalise(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [canonicalise(item) for item in sorted(value, key=repr)]
    raise SerialisationError(
        "value has no canonical JSON representation", received_type=type(value).__name__
    )


def canonical_dumps(value: Any) -> str:
    """Serialise any HELIOS value to canonical JSON text."""
    return json.dumps(
        canonicalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_loads(text: str) -> Any:
    """Parse canonical JSON, reading every JSON number as an exact Decimal."""
    if not isinstance(text, str):
        raise SerialisationError(
            "expected JSON text", received_type=type(text).__name__
        )
    try:
        return json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SerialisationError("malformed JSON", detail=str(exc)) from exc


def _reject_constant(name: str) -> Any:
    raise SerialisationError("JSON contains a non-finite constant", constant=name)
