"""Proximity to the swing high or swing low of a declared lookback.

This atom reports WHERE price is, not what to do about it. ``LONG`` means the
close sits at the upper extreme of the declared lookback and ``SHORT`` the
lower one; that is a statement of location in the contract's own directional
vocabulary, deliberately not a thesis about what happens next. Inventing the
latter would be inventing trading logic, which HELIOS must never do.

The swing extremes are strategy-local geometry over the highs and lows HERMES
supplied. The extreme a live match is measured against is remembered in the
published evidence, so a later bar making a new extreme cannot silently move
the level the state depends on.
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
    quantised_ratio,
    unit_interval,
)

_LEVEL_KEY = "swing_level"
_ONE = Decimal(1)


class SwingProximityAtom(AtomicStrategy):
    """The close sitting within a declared distance of a swing extreme."""

    ATOM_NAME = "swing_proximity"
    REQUIRED_FIELDS = ("high", "low", "close")
    MIN_LOOKBACK = 2
    DIRECTIONAL = True
    SUMMARY = (
        "The close sits within the declared distance of the swing high or swing low "
        "of the declared lookback."
    )
    PARAMETERS = (
        ParameterRequirement(
            name="lookback_bars",
            type=ParameterType.INTEGER,
            minimum=Decimal(2),
            description="Bars the swing extremes are measured over.",
        ),
        ParameterRequirement(
            name="max_distance",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            description=(
                "How near the close must be to a swing extreme, in the instrument's "
                "own price units."
            ),
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
                "the package declares less history than the swing lookback it asks "
                "this strategy to measure",
                strategy_id=str(package.identity.strategy_id),
                declared_lookback=declared,
                lookback_bars=bars,
            )

    def _closeness(self, distance: Decimal, limit: Decimal) -> Decimal:
        """1 at the level itself, 0 at the edge of the declared distance."""
        if limit == 0:
            return _ONE if distance == 0 else Decimal(0)
        return unit_interval(_ONE - quantised_ratio(distance, limit))

    def assess(self, reading: AtomReading) -> AtomVerdict:
        bars_wanted = reading.integer_parameter("lookback_bars")
        limit = reading.decimal_parameter("max_distance")

        bars = reading.frames[-bars_wanted:]
        latest = bars[-1]
        swing_high = max(frame.fact("high") for frame in bars)
        swing_low = min(frame.fact("low") for frame in bars)
        close = latest.fact("close")
        to_high = swing_high - close
        to_low = close - swing_low

        evidence: dict[str, Any] = {
            "swing_high": swing_high,
            "swing_low": swing_low,
            "swing_bars": len(bars),
            "close": close,
            "distance_to_swing_high": to_high,
            "distance_to_swing_low": to_low,
            "max_distance": limit,
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
                    explanation="the live match carries no swing level to measure against",
                    invalidation=(
                        "the published evidence no longer describes a level this "
                        "strategy can measure against"
                    ),
                )
            evidence[_LEVEL_KEY] = level
            passed = close > level if held is Direction.LONG else close < level
            if passed:
                return AtomVerdict(
                    holds=False,
                    direction=held,
                    evidence=evidence,
                    explanation=(
                        f"the close {close} has moved beyond the swing level {level} it "
                        f"was near"
                    ),
                    invalidation=(
                        "the close moved beyond the swing level; the location the match "
                        "described no longer exists"
                    ),
                )
            distance = abs(level - close)
            if distance > limit:
                return AtomVerdict(
                    holds=False,
                    direction=held,
                    evidence=evidence,
                    explanation=(
                        f"the close {close} has moved {distance} away from the swing "
                        f"level {level}, past the declared {limit}"
                    ),
                )
            return AtomVerdict(
                holds=True,
                direction=held,
                strength=self._closeness(distance, limit),
                evidence=evidence,
                explanation=(
                    f"the close {close} is still {distance} from the swing level "
                    f"{level}, within the declared {limit}"
                ),
            )

        if to_high == to_low:
            return AtomVerdict(
                holds=False,
                forming=to_high <= limit,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"the close {close} is exactly equidistant from the swing high "
                    f"{swing_high} and the swing low {swing_low}; the location is "
                    f"ambiguous and this strategy will not guess"
                ),
            )
        if to_high < to_low and to_high <= limit:
            evidence[_LEVEL_KEY] = swing_high
            return AtomVerdict(
                holds=True,
                direction=Direction.LONG,
                strength=self._closeness(to_high, limit),
                evidence=evidence,
                explanation=(
                    f"the close {close} is {to_high} below the {len(bars)}-bar swing "
                    f"high {swing_high}, within the declared {limit}"
                ),
            )
        if to_low < to_high and to_low <= limit:
            evidence[_LEVEL_KEY] = swing_low
            return AtomVerdict(
                holds=True,
                direction=Direction.SHORT,
                strength=self._closeness(to_low, limit),
                evidence=evidence,
                explanation=(
                    f"the close {close} is {to_low} above the {len(bars)}-bar swing low "
                    f"{swing_low}, within the declared {limit}"
                ),
            )

        reached_high = swing_high - latest.fact("high") <= limit
        reached_low = latest.fact("low") - swing_low <= limit
        if reached_high or reached_low:
            edge = "high" if reached_high else "low"
            return AtomVerdict(
                holds=False,
                forming=True,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"the bar reached within {limit} of the {len(bars)}-bar swing "
                    f"{edge} but its close {close} did not"
                ),
            )
        return AtomVerdict(
            holds=False,
            direction=Direction.NEUTRAL,
            evidence=evidence,
            explanation=(
                f"the close {close} is {to_high} from the swing high and {to_low} from "
                f"the swing low, outside the declared {limit} of either"
            ),
        )
