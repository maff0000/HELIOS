"""UTC time handling.

UTC is canonical everywhere in HELIOS: in contracts, in logs, in fixtures and
in every serialised output. Local time does not appear in anything HELIOS
persists, logs or publishes. Naive datetimes are rejected loudly rather than
being assumed to mean UTC.
"""

from __future__ import annotations

from datetime import datetime, timezone

from helios.errors import ContractViolationError

#: Fixed-width ISO-8601 UTC representation. Microsecond precision is always
#: emitted so that serialised output is byte-stable for equal instants.
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


def utc_now() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime, *, field: str = "timestamp") -> datetime:
    """Return ``value`` normalised to UTC, failing loudly on anything ambiguous.

    A naive datetime is rejected: HELIOS will not guess which zone a caller
    meant. An aware datetime in another zone is converted to UTC.
    """
    if not isinstance(value, datetime):
        raise ContractViolationError(
            "expected a datetime", field=field, received_type=type(value).__name__
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ContractViolationError(
            "naive datetime rejected; an explicit UTC offset is required",
            field=field,
            value=value.isoformat(),
        )
    return value.astimezone(timezone.utc)


def to_iso8601_utc(value: datetime) -> str:
    """Serialise an instant as a fixed-width ISO-8601 UTC string ending in 'Z'."""
    return ensure_utc(value).strftime(_ISO_FORMAT) + "Z"


def from_iso8601_utc(value: str, *, field: str = "timestamp") -> datetime:
    """Parse an ISO-8601 UTC string. A missing/ambiguous offset fails loudly."""
    if not isinstance(value, str):
        raise ContractViolationError(
            "expected an ISO-8601 UTC string", field=field, received_type=type(value).__name__
        )
    text = value.strip()
    if not text.endswith("Z") and "+" not in text[10:] and "-" not in text[10:]:
        raise ContractViolationError(
            "timestamp has no UTC offset; HELIOS does not assume a timezone",
            field=field,
            value=value,
        )
    normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ContractViolationError(
            "malformed ISO-8601 timestamp", field=field, value=value, detail=str(exc)
        ) from exc
    return ensure_utc(parsed, field=field)
