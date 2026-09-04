"""Rejection wick.

A candle whose wick dominates its range. This is candle geometry intrinsic to
the strategy definition — "is this bar's wick more than the declared fraction
of this bar's range" — not a reusable market indicator, so it belongs here
rather than in HERMES.

The condition is a property of ONE bar. It therefore matches on that bar and
is invalidated on the next, which is the honest lifecycle for a single-bar
observation: there is no sense in which yesterday's wick is still true today.
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


class RejectionWickAtom(AtomicStrategy):
    """A bar whose upper or lower wick is at least the declared share of its range."""

    ATOM_NAME = "rejection_wick"
    REQUIRED_FIELDS = ("open", "high", "low", "close")
    MIN_LOOKBACK = 1
    DIRECTIONAL = True
    SUMMARY = (
        "One bar's upper or lower wick occupies at least the declared fraction of "
        "that bar's whole range."
    )
    PARAMETERS = (
        ParameterRequirement(
            name="min_wick_ratio",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            maximum=Decimal(1),
            description="Wick length as a fraction of the bar's high-to-low range.",
        ),
    )

    def assess(self, reading: AtomReading) -> AtomVerdict:
        minimum = reading.decimal_parameter("min_wick_ratio")
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
                evidence={"bar_range": span, "min_wick_ratio": minimum},
                explanation=(
                    "the bar has no range, so a wick cannot be expressed as a fraction "
                    "of it; this strategy will not divide by zero to produce a state"
                ),
            )

        body_top = max(opened, close)
        body_bottom = min(opened, close)
        upper = high - body_top
        lower = body_bottom - low
        upper_ratio = quantised_ratio(upper, span)
        lower_ratio = quantised_ratio(lower, span)

        evidence: dict[str, Any] = {
            "bar_range": span,
            "upper_wick": upper,
            "lower_wick": lower,
            "upper_wick_ratio": upper_ratio,
            "lower_wick_ratio": lower_ratio,
            "min_wick_ratio": minimum,
        }

        upper_qualifies = upper_ratio >= minimum
        lower_qualifies = lower_ratio >= minimum

        if upper_qualifies and lower_qualifies and upper_ratio == lower_ratio:
            return AtomVerdict(
                holds=False,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"both wicks occupy an identical {upper_ratio} of the bar's range; "
                    f"neither dominates and this strategy will not guess between them"
                ),
            )
        if upper_qualifies and upper_ratio > lower_ratio:
            return AtomVerdict(
                holds=True,
                direction=Direction.SHORT,
                strength=upper_ratio,
                evidence=evidence,
                explanation=(
                    f"the upper wick {upper} is {upper_ratio} of the bar's range {span}, "
                    f"at or beyond the declared {minimum}"
                ),
            )
        if lower_qualifies and lower_ratio > upper_ratio:
            return AtomVerdict(
                holds=True,
                direction=Direction.LONG,
                strength=lower_ratio,
                evidence=evidence,
                explanation=(
                    f"the lower wick {lower} is {lower_ratio} of the bar's range {span}, "
                    f"at or beyond the declared {minimum}"
                ),
            )
        return AtomVerdict(
            holds=False,
            direction=Direction.NEUTRAL,
            evidence=evidence,
            explanation=(
                f"neither wick dominates the bar: upper {upper_ratio}, lower "
                f"{lower_ratio}, both under the declared {minimum}"
            ),
        )
