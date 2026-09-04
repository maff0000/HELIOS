"""No-wick directional candle.

A bar that opened at one extreme and closed at the other, so that almost all
of its range is body. Like the rejection wick, this is candle geometry
intrinsic to the strategy definition rather than a reusable market indicator.

The direction is simply which way the body points. A bar with no body at all
has no direction, so the condition does not hold and the strategy says why
instead of picking one.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from helios.contracts.state import Direction
from helios.spec.model import ParameterType
from helios.strategies.base import (
    AtomicStrategy,
    AtomReading,
    AtomVerdict,
    ParameterRequirement,
    quantised_ratio,
)


class NoWickCandleAtom(AtomicStrategy):
    """A bar that is nearly all body and points in one direction."""

    ATOM_NAME = "no_wick_candle"
    REQUIRED_FIELDS = ("open", "high", "low", "close")
    MIN_LOOKBACK = 1
    DIRECTIONAL = True
    SUMMARY = (
        "One bar whose wicks are both within the declared maximum and whose body "
        "occupies at least the declared share of its range."
    )
    PARAMETERS = (
        ParameterRequirement(
            name="max_wick_ratio",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            maximum=Decimal(1),
            description="Largest wick, as a fraction of the bar's range, still allowed.",
        ),
        ParameterRequirement(
            name="min_body_ratio",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            maximum=Decimal(1),
            description="Smallest body, as a fraction of the bar's range, that counts.",
        ),
    )

    def assess(self, reading: AtomReading) -> AtomVerdict:
        max_wick = reading.decimal_parameter("max_wick_ratio")
        min_body = reading.decimal_parameter("min_body_ratio")
        latest = reading.latest

        opened = latest.fact("open")
        high = latest.fact("high")
        low = latest.fact("low")
        close = latest.fact("close")
        span = high - low

        if span == 0:
            return AtomVerdict(
                holds=False,
                direction=Direction.NEUTRAL,
                evidence={"bar_range": span, "max_wick_ratio": max_wick},
                explanation=(
                    "the bar has no range, so body and wick cannot be expressed as "
                    "fractions of it; this strategy will not divide by zero to produce "
                    "a state"
                ),
            )

        body_top = max(opened, close)
        body_bottom = min(opened, close)
        upper_ratio = quantised_ratio(high - body_top, span)
        lower_ratio = quantised_ratio(body_bottom - low, span)
        body_ratio = quantised_ratio(body_top - body_bottom, span)

        evidence: dict[str, Any] = {
            "bar_range": span,
            "upper_wick_ratio": upper_ratio,
            "lower_wick_ratio": lower_ratio,
            "body_ratio": body_ratio,
            "max_wick_ratio": max_wick,
            "min_body_ratio": min_body,
        }

        if close == opened:
            return AtomVerdict(
                holds=False,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"the bar opened and closed at {close}, so it has no body and no "
                    f"direction to report"
                ),
            )

        direction = Direction.LONG if close > opened else Direction.SHORT
        wicks_clean = upper_ratio <= max_wick and lower_ratio <= max_wick
        body_full = body_ratio >= min_body

        if wicks_clean and body_full:
            return AtomVerdict(
                holds=True,
                direction=direction,
                strength=body_ratio,
                evidence=evidence,
                explanation=(
                    f"the bar's body is {body_ratio} of its range {span} and both wicks "
                    f"(upper {upper_ratio}, lower {lower_ratio}) are within the declared "
                    f"{max_wick}; the body points {direction.value}"
                ),
            )
        if body_full:
            return AtomVerdict(
                holds=False,
                forming=True,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"the bar's body is {body_ratio} of its range but a wick (upper "
                    f"{upper_ratio}, lower {lower_ratio}) exceeds the declared "
                    f"{max_wick}"
                ),
            )
        return AtomVerdict(
            holds=False,
            direction=Direction.NEUTRAL,
            evidence=evidence,
            explanation=(
                f"the bar's body is only {body_ratio} of its range, under the declared "
                f"{min_body}"
            ),
        )
