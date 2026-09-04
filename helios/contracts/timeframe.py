"""Timeframe vocabulary.

A :class:`Timeframe` knows its own duration. That duration is the *only*
source from which freshness limits are derived, so freshness rules scale
correctly across timeframes without a hard-coded table.

Enumerating the timeframes themselves is data vocabulary and is fine. What is
deliberately absent is any global mapping from a timeframe to a semantic role
(CONTEXT / LOCATION / CONFIRMATION / TRIGGER) — that mapping belongs to an
individual strategy package.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic_core import core_schema

from helios.errors import ContractViolationError


class Timeframe(Enum):
    """A HERMES bar timeframe, carrying its own duration.

    ``value`` is the canonical HELIOS code (``"M15"``). ``hermes_code`` is the
    form the HERMES feed uses (``"15m"``); both parse.
    """

    M1 = ("M1", "1m", timedelta(minutes=1))
    M5 = ("M5", "5m", timedelta(minutes=5))
    M15 = ("M15", "15m", timedelta(minutes=15))
    H1 = ("H1", "1h", timedelta(hours=1))
    H4 = ("H4", "4h", timedelta(hours=4))
    D1 = ("D1", "1d", timedelta(days=1))

    def __init__(self, code: str, hermes_code: str, duration: timedelta) -> None:
        self._code = code
        self._hermes_code = hermes_code
        self._duration = duration

    @property
    def code(self) -> str:
        """Canonical HELIOS code, e.g. ``"H4"``."""
        return self._code

    @property
    def hermes_code(self) -> str:
        """The code the HERMES feed uses, e.g. ``"4h"``."""
        return self._hermes_code

    @property
    def duration(self) -> timedelta:
        """Wall-clock length of one bar on this timeframe."""
        return self._duration

    @property
    def seconds(self) -> int:
        return int(self._duration.total_seconds())

    def __str__(self) -> str:
        return self._code

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Timeframe):
            return NotImplemented
        return self._duration < other._duration

    def __le__(self, other: Any) -> bool:
        if not isinstance(other, Timeframe):
            return NotImplemented
        return self._duration <= other._duration

    def __gt__(self, other: Any) -> bool:
        if not isinstance(other, Timeframe):
            return NotImplemented
        return self._duration > other._duration

    def __ge__(self, other: Any) -> bool:
        if not isinstance(other, Timeframe):
            return NotImplemented
        return self._duration >= other._duration

    @classmethod
    def parse(cls, value: Any) -> "Timeframe":
        """Parse a HELIOS or HERMES timeframe code, failing loudly on anything else."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ContractViolationError(
                "malformed timeframe: expected a string code",
                received_type=type(value).__name__,
                supported=[member.code for member in cls],
            )
        candidate = value.strip().upper()
        for member in cls:
            if candidate in (member.code, member.hermes_code.upper()):
                return member
        raise ContractViolationError(
            "unsupported timeframe",
            value=value,
            supported=[member.code for member in cls],
        )

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        """Accept ``"H4"``/``"4h"``/:class:`Timeframe`; always emit ``"H4"``."""
        return core_schema.no_info_plain_validator_function(
            cls.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda member: member.code,
                return_schema=core_schema.str_schema(),
                when_used="always",
            ),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> Any:
        return {"type": "string", "enum": [member.code for member in cls], "title": "Timeframe"}

    def is_aligned(self, moment: datetime) -> bool:
        """Whether ``moment`` is a plausible bar-open instant for this timeframe.

        HELIOS checks the sub-hour grid only. Session-anchored H4/D1 bars have
        a venue-dependent daily anchor, so requiring epoch alignment there
        would reject legitimate HERMES facts. Seconds and microseconds must be
        zero on every timeframe, and sub-hour bars must sit on their minute
        grid; that catches junk timestamps without inventing venue rules.
        """
        if moment.second or moment.microsecond:
            return False
        if self._duration < timedelta(hours=1):
            return moment.minute % int(self._duration.total_seconds() // 60) == 0
        return moment.minute == 0

    def close_time(self, open_time: datetime) -> datetime:
        """The instant at which the bar opening at ``open_time`` completes."""
        return open_time + self._duration


ALL_TIMEFRAMES: tuple[Timeframe, ...] = tuple(
    sorted(Timeframe, key=lambda member: member.duration)
)
