"""Freshness and validity rules for market facts.

Missing or stale facts are never silently defaulted and never silently zeroed.
Every required fact is checked against an explicit limit derived from the
timeframe's own duration, and the resulting verdict is published in the output
envelope so FALCON can see exactly how fresh the inputs behind a state were.

Every number here comes from external configuration (see
:mod:`helios.config`). There are no policy defaults in this source file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Mapping, Optional

from helios.clock import ensure_utc
from helios.contracts.market_fact import MarketFactFrame
from helios.contracts.timeframe import Timeframe
from helios.contracts.window import MarketFactWindow
from helios.errors import ContractViolationError, MissingFactError, StaleFactError


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    """How old a fact may be before HELIOS refuses to evaluate against it.

    The limit for a timeframe is ``duration * max_age_multiplier + grace``,
    measured from the bar's CLOSE instant, so one policy scales correctly from
    M1 to D1 without a hard-coded per-timeframe table. ``overrides`` exists for
    the case where one timeframe genuinely needs a different rule; it too comes
    from configuration.
    """

    max_age_multiplier: str
    grace: timedelta
    allow_incomplete_frames: bool
    overrides: Mapping[Timeframe, timedelta] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            multiplier = float(self.max_age_multiplier)
        except (TypeError, ValueError) as exc:
            raise ContractViolationError(
                "freshness max_age_multiplier must be numeric",
                value=repr(self.max_age_multiplier),
            ) from exc
        if multiplier <= 0:
            raise ContractViolationError(
                "freshness max_age_multiplier must be greater than zero",
                value=repr(self.max_age_multiplier),
            )
        if not isinstance(self.grace, timedelta) or self.grace < timedelta(0):
            raise ContractViolationError(
                "freshness grace must be a non-negative duration", value=repr(self.grace)
            )
        if not isinstance(self.allow_incomplete_frames, bool):
            raise ContractViolationError(
                "allow_incomplete_frames must be a boolean",
                value=repr(self.allow_incomplete_frames),
            )
        normalised: dict[Timeframe, timedelta] = {}
        for key, value in dict(self.overrides).items():
            timeframe = Timeframe.parse(key)
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ContractViolationError(
                    "freshness override must be a positive duration",
                    timeframe=timeframe.code,
                    value=repr(value),
                )
            normalised[timeframe] = value
        object.__setattr__(self, "overrides", MappingProxyType(normalised))

    @property
    def multiplier(self) -> float:
        return float(self.max_age_multiplier)

    def max_age_for(self, timeframe: Timeframe) -> timedelta:
        """The maximum permitted age, measured from the bar's close instant."""
        override = self.overrides.get(timeframe)
        if override is not None:
            return override
        return timedelta(seconds=timeframe.seconds * self.multiplier) + self.grace


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """The explicit, publishable outcome of one freshness check."""

    instrument: str
    timeframe: Timeframe
    frame_timestamp_utc: datetime
    evaluated_at_utc: datetime
    age: timedelta
    max_age: timedelta
    is_fresh: bool
    is_complete: bool
    source: str
    schema_version: str

    @property
    def age_seconds(self) -> int:
        return int(self.age.total_seconds())

    @property
    def max_age_seconds(self) -> int:
        return int(self.max_age.total_seconds())


def assess_frame(
    frame: MarketFactFrame, policy: FreshnessPolicy, *, now_utc: datetime
) -> FreshnessVerdict:
    """Measure a frame's age against the policy. Reports; does not raise."""
    now_utc = ensure_utc(now_utc, field="now_utc")
    max_age = policy.max_age_for(frame.timeframe)
    age = now_utc - frame.close_time_utc
    if age < timedelta(0):
        age = timedelta(0)
    return FreshnessVerdict(
        instrument=str(frame.instrument),
        timeframe=frame.timeframe,
        frame_timestamp_utc=frame.timestamp_utc,
        evaluated_at_utc=now_utc,
        age=age,
        max_age=max_age,
        is_fresh=age <= max_age,
        is_complete=frame.candle.complete,
        source=str(frame.provenance.source),
        schema_version=frame.provenance.schema_version,
    )


def require_fresh_frame(
    frame: MarketFactFrame, policy: FreshnessPolicy, *, now_utc: datetime
) -> FreshnessVerdict:
    """Assert a frame is usable, raising loudly when it is not."""
    verdict = assess_frame(frame, policy, now_utc=now_utc)
    if not verdict.is_fresh:
        raise StaleFactError(
            "required market fact is stale",
            instrument=verdict.instrument,
            timeframe=verdict.timeframe.code,
            frame_timestamp_utc=verdict.frame_timestamp_utc.isoformat(),
            age_seconds=verdict.age_seconds,
            max_age_seconds=verdict.max_age_seconds,
        )
    if not verdict.is_complete and not policy.allow_incomplete_frames:
        raise StaleFactError(
            "required market fact is an incomplete bar and policy forbids it",
            instrument=verdict.instrument,
            timeframe=verdict.timeframe.code,
            frame_timestamp_utc=verdict.frame_timestamp_utc.isoformat(),
        )
    return verdict


def require_fresh_window(
    window: Optional[MarketFactWindow],
    policy: FreshnessPolicy,
    *,
    now_utc: datetime,
    role: Optional[str] = None,
) -> FreshnessVerdict:
    """Assert a required window exists and its latest frame is usable."""
    if window is None:
        raise MissingFactError(
            "a required market-fact window is absent",
            role=role or "<unspecified>",
        )
    return require_fresh_frame(window.latest, policy, now_utc=now_utc)
