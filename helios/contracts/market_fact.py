"""HERMES-compatible market-fact contract.

HERMES is the market-fact authority. HELIOS consumes ``ema_50``, ``rsi_14``,
``atr_14`` and the rest as *given facts* and never recomputes them; there is
deliberately no reusable indicator library in this package. Strategy-local
derived logic (for example "is this candle's wick more than 60% of its range")
belongs to an individual strategy definition, not here.

Every field name below mirrors the HERMES fact schema so that a real feed maps
onto this contract without translation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import field_validator, model_validator

from helios.contracts._fields import DecimalValue, HeliosModel, UtcDatetime
from helios.contracts._tokens import Instrument, Regime, Session, SourceName
from helios.contracts.timeframe import Timeframe
from helios.errors import ContractViolationError, MissingFactError

#: Candle fields carried by every HERMES market fact.
CANDLE_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "complete", "source")

#: Indicator/context fields HERMES supplies alongside the candle. Any of them
#: may be absent on a given frame.
INDICATOR_FIELDS: tuple[str, ...] = (
    "rsi_14",
    "ema_9",
    "ema_21",
    "ema_50",
    "ema_200",
    "atr_14",
    "regime",
    "session",
)

#: Every fact field a strategy package may declare as a required input.
FACT_FIELDS: tuple[str, ...] = CANDLE_FIELDS + INDICATOR_FIELDS

#: The market-fact contract version HELIOS implements. A HERMES schema change
#: must be matched by a deliberate bump here.
MARKET_FACT_SCHEMA_VERSION = "helios.market_fact/1.0.0"


class Candle(HeliosModel):
    """One OHLCV bar exactly as HERMES reports it."""

    open: DecimalValue
    high: DecimalValue
    low: DecimalValue
    close: DecimalValue
    volume: DecimalValue
    complete: bool
    source: SourceName

    @field_validator("open", "high", "low", "close")
    @classmethod
    def _prices_are_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ContractViolationError("candle price must be positive", value=str(value))
        return value

    @field_validator("volume")
    @classmethod
    def _volume_is_not_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ContractViolationError("candle volume must not be negative", value=str(value))
        return value

    @model_validator(mode="after")
    def _bounds_are_coherent(self) -> "Candle":
        if self.high < self.low:
            raise ContractViolationError(
                "malformed candle: high is below low",
                high=str(self.high),
                low=str(self.low),
            )
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ContractViolationError(
                "malformed candle: open/close outside the high/low range",
                open=str(self.open),
                high=str(self.high),
                low=str(self.low),
                close=str(self.close),
            )
        return self

    @property
    def range(self) -> Decimal:
        """High minus low. Present because every candle geometry needs it; it
        is arithmetic on one given fact, not a market indicator."""
        return self.high - self.low


class IndicatorSet(HeliosModel):
    """Indicator and context facts for one frame.

    Every field is optional because not every frame carries every indicator —
    ``ema_200`` does not exist until 200 bars of history do. Absence is
    represented as ``None`` and is never silently replaced with zero. A
    strategy that needs an indicator asks for it with :meth:`require`, which
    fails loudly when it is missing.
    """

    rsi_14: Optional[DecimalValue] = None
    ema_9: Optional[DecimalValue] = None
    ema_21: Optional[DecimalValue] = None
    ema_50: Optional[DecimalValue] = None
    ema_200: Optional[DecimalValue] = None
    atr_14: Optional[DecimalValue] = None
    regime: Optional[Regime] = None
    session: Optional[Session] = None

    @field_validator("rsi_14")
    @classmethod
    def _rsi_in_range(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and not (Decimal(0) <= value <= Decimal(100)):
            raise ContractViolationError("rsi_14 outside 0..100", value=str(value))
        return value

    @field_validator("atr_14")
    @classmethod
    def _atr_is_not_negative(cls, value: Optional[Decimal]) -> Optional[Decimal]:
        if value is not None and value < 0:
            raise ContractViolationError("atr_14 must not be negative", value=str(value))
        return value

    @property
    def available(self) -> frozenset[str]:
        """Names of the indicators actually present on this frame."""
        return frozenset(name for name in INDICATOR_FIELDS if getattr(self, name) is not None)

    def get(self, name: str) -> Any:
        if name not in INDICATOR_FIELDS:
            raise ContractViolationError(
                "unknown indicator field", field=name, known=list(INDICATOR_FIELDS)
            )
        return getattr(self, name)

    def require(self, *names: str) -> tuple[Any, ...]:
        """Return the named indicators, raising if any is unknown or absent."""
        missing = []
        values = []
        for name in names:
            value = self.get(name)
            if value is None:
                missing.append(name)
            values.append(value)
        if missing:
            raise MissingFactError(
                "required indicator facts are absent from this frame",
                missing=sorted(missing),
                available=sorted(self.available),
            )
        return tuple(values)


class Provenance(HeliosModel):
    """Where a fact came from and when it was seen.

    ``observed_at_utc`` is when the upstream authority produced/observed the
    fact; ``ingested_at_utc`` is when HELIOS received it. Both are published in
    the output envelope's freshness metadata.
    """

    source: SourceName
    schema_version: str
    observed_at_utc: UtcDatetime
    ingested_at_utc: UtcDatetime

    @field_validator("schema_version")
    @classmethod
    def _schema_version_is_qualified(cls, value: str) -> str:
        if not isinstance(value, str) or "/" not in value or not value.strip():
            raise ContractViolationError(
                "malformed schema_version: expected '<namespace>/<major.minor.patch>'",
                value=value,
            )
        return value

    @model_validator(mode="after")
    def _ingestion_follows_observation(self) -> "Provenance":
        if self.ingested_at_utc < self.observed_at_utc:
            raise ContractViolationError(
                "malformed provenance: ingested before observed",
                observed_at_utc=self.observed_at_utc.isoformat(),
                ingested_at_utc=self.ingested_at_utc.isoformat(),
            )
        return self


class MarketFactFrame(HeliosModel):
    """One instrument, one timeframe, one bar: the atom of HELIOS input.

    ``timestamp_utc`` is the bar OPEN instant, as HERMES publishes it.
    """

    instrument: Instrument
    timeframe: Timeframe
    timestamp_utc: UtcDatetime
    candle: Candle
    indicators: IndicatorSet
    provenance: Provenance

    @model_validator(mode="after")
    def _timestamp_fits_the_timeframe(self) -> "MarketFactFrame":
        if not self.timeframe.is_aligned(self.timestamp_utc):
            raise ContractViolationError(
                "frame timestamp is not a plausible bar-open instant for its timeframe",
                timeframe=self.timeframe.code,
                timestamp_utc=self.timestamp_utc.isoformat(),
            )
        return self

    @property
    def close_time_utc(self) -> datetime:
        """The instant at which this bar completes."""
        return self.timeframe.close_time(self.timestamp_utc)

    def fact(self, name: str) -> Any:
        """Read one named fact, failing loudly if unknown or absent.

        This is the only accessor strategies should use for declared required
        inputs: it turns a missing fact into a :class:`MissingFactError`
        instead of a silent ``None`` that later reads as zero.
        """
        if name in CANDLE_FIELDS:
            value = getattr(self.candle, name)
        elif name in INDICATOR_FIELDS:
            value = self.indicators.get(name)
        else:
            raise ContractViolationError(
                "unknown market-fact field", field=name, known=list(FACT_FIELDS)
            )
        if value is None:
            raise MissingFactError(
                "required market fact is absent from this frame",
                field=name,
                instrument=str(self.instrument),
                timeframe=self.timeframe.code,
                timestamp_utc=self.timestamp_utc.isoformat(),
            )
        return value

    @property
    def key(self) -> tuple[str, str, str]:
        """The ``(instrument, timeframe, timestamp)`` identity of this fact."""
        return (str(self.instrument), self.timeframe.code, self.timestamp_utc.isoformat())
