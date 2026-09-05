"""Breakout of the recent range.

The range is strategy-local geometry over facts HERMES supplied: the highest
high and lowest low of the declared number of bars immediately before the bar
being evaluated. ``atr_14`` is consumed as a given fact and used only as the
scale for the declared clearance; HELIOS does not compute it.

The level a breakout cleared is remembered in the published evidence for as
long as the match is live. It has to be: the range recomputed on the next bar
would swallow the breakout bar itself and the level would drift upward behind
the price. Publishing it rather than hiding it in an attribute keeps the whole
evaluation a fold over (previous envelope, facts) and lets a consumer see the
exact level the state depends on.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from helios.contracts.state import Direction
from helios.errors import StrategySpecError
from helios.spec.model import ParameterType, StrategyPackage
from helios.strategies.base import (
    AtomicStrategy,
    AtomReading,
    AtomVerdict,
    ParameterRequirement,
    proportion_beyond,
)

_LEVEL_KEY = "range_level"


class RangeBreakoutAtom(AtomicStrategy):
    """A close beyond the prior range, by a declared multiple of ``atr_14``."""

    ATOM_NAME = "range_breakout"
    REQUIRED_FIELDS = ("high", "low", "close", "atr_14")
    MIN_LOOKBACK = 2
    DIRECTIONAL = True
    SUMMARY = (
        "The close clears the highest high or lowest low of the prior bars by at "
        "least the declared multiple of atr_14, and stays clear of that level."
    )
    PARAMETERS = (
        ParameterRequirement(
            name="lookback_bars",
            type=ParameterType.INTEGER,
            minimum=Decimal(2),
            description=(
                "Bars examined, including the one being evaluated. The range is "
                "measured over the ones before it."
            ),
        ),
        ParameterRequirement(
            name="min_atr_multiple",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            description="Clearance beyond the range, as a multiple of atr_14.",
        ),
    )

    @classmethod
    def validate_binding(
        cls, package: StrategyPackage, parameters: Mapping[str, Any]
    ) -> None:
        super().validate_binding(package, parameters)
        declared = package.inputs[0].lookback
        bars = parameters["lookback_bars"]
        if declared < bars:
            raise StrategySpecError(
                "the package declares less history than the range it asks this "
                "strategy to measure; HELIOS will not measure a shorter range than "
                "was declared",
                strategy_id=str(package.identity.strategy_id),
                declared_lookback=declared,
                lookback_bars=bars,
            )

    def assess(self, reading: AtomReading) -> AtomVerdict:
        bars_wanted = reading.integer_parameter("lookback_bars")
        multiple = reading.decimal_parameter("min_atr_multiple")

        bars = reading.frames[-bars_wanted:]
        prior = bars[:-1]
        latest = bars[-1]

        range_high = max(frame.fact("high") for frame in prior)
        range_low = min(frame.fact("low") for frame in prior)
        close = latest.fact("close")
        atr = latest.fact("atr_14")
        clearance = atr * multiple

        evidence: dict[str, Any] = {
            "range_high": range_high,
            "range_low": range_low,
            "range_bars": len(prior),
            "close": close,
            "atr_14": atr,
            "clearance": clearance,
            _LEVEL_KEY: None,
        }

        if reading.previous_is_live:
            level = reading.carried_decimal(_LEVEL_KEY)
            held = reading.previous_direction
            if level is None or not held.is_directional:
                return AtomVerdict(
                    holds=False,
                    direction=held,
                    evidence=evidence,
                    explanation=(
                        "the live match carries no cleared level to measure against"
                    ),
                    invalidation=(
                        "the published evidence no longer describes a level this "
                        "strategy can measure against"
                    ),
                )
            evidence[_LEVEL_KEY] = level
            beyond = close > level if held is Direction.LONG else close < level
            distance = abs(close - level)
            if not beyond:
                return AtomVerdict(
                    holds=False,
                    direction=held,
                    evidence=evidence,
                    explanation=(
                        f"the close {close} has returned inside the level {level} this "
                        f"{held.value} breakout cleared"
                    ),
                    invalidation=(
                        "the close returned inside the level the breakout cleared"
                    ),
                )
            return AtomVerdict(
                holds=True,
                direction=held,
                strength=proportion_beyond(distance, atr),
                evidence=evidence,
                explanation=(
                    f"the close {close} remains {distance} clear of the level {level} "
                    f"this {held.value} breakout cleared"
                ),
            )

        if close > range_high + clearance:
            evidence[_LEVEL_KEY] = range_high
            return AtomVerdict(
                holds=True,
                direction=Direction.LONG,
                strength=proportion_beyond(close - range_high, atr),
                evidence=evidence,
                explanation=(
                    f"the close {close} cleared the {len(prior)}-bar range high "
                    f"{range_high} by more than {clearance} "
                    f"({multiple} x atr_14 {atr})"
                ),
            )
        if close < range_low - clearance:
            evidence[_LEVEL_KEY] = range_low
            return AtomVerdict(
                holds=True,
                direction=Direction.SHORT,
                strength=proportion_beyond(range_low - close, atr),
                evidence=evidence,
                explanation=(
                    f"the close {close} broke below the {len(prior)}-bar range low "
                    f"{range_low} by more than {clearance} "
                    f"({multiple} x atr_14 {atr})"
                ),
            )

        pierced_high = latest.fact("high") > range_high
        pierced_low = latest.fact("low") < range_low
        if pierced_high or pierced_low:
            edge = "high" if pierced_high else "low"
            return AtomVerdict(
                holds=False,
                forming=True,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"the bar traded through the {len(prior)}-bar range {edge} but its "
                    f"close {close} did not clear it by the declared {clearance}"
                ),
            )
        return AtomVerdict(
            holds=False,
            direction=Direction.NEUTRAL,
            evidence=evidence,
            explanation=(
                f"the close {close} is inside the {len(prior)}-bar range "
                f"{range_low}..{range_high}"
            ),
        )
