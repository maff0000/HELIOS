"""Golden / death cross.

Two moving averages HERMES publishes change sides. HELIOS consumes ``ema_50``
and ``ema_200`` as given facts and never computes them; all this module does
is compare the two numbers it was handed on consecutive bars.

What makes this a *cross* rather than "one average happens to be above the
other" is that the side changed between the previous declared bar and the one
being evaluated. That distinction is why a cold start publishes DORMANT rather
than claiming a match for a cross HELIOS never observed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from helios.contracts.market_fact import MarketFactFrame
from helios.contracts.state import Direction
from helios.spec.model import ParameterType
from helios.strategies.base import (
    AtomicStrategy,
    AtomReading,
    AtomVerdict,
    ParameterRequirement,
    proportion_beyond,
)

_FAST = "ema_50"
_SLOW = "ema_200"


def _side(frame: MarketFactFrame) -> Optional[Direction]:
    """Which side of the slower average the faster one sits on."""
    separation = frame.fact(_FAST) - frame.fact(_SLOW)
    if separation > 0:
        return Direction.LONG
    if separation < 0:
        return Direction.SHORT
    return None


class GoldenCrossAtom(AtomicStrategy):
    """``ema_50`` changing sides against ``ema_200``, by a declared margin."""

    ATOM_NAME = "golden_cross"
    REQUIRED_FIELDS = ("close", _FAST, _SLOW)
    MIN_LOOKBACK = 2
    DIRECTIONAL = True
    CONDITION_IS_AN_EDGE = True
    SUMMARY = (
        "ema_50 crosses ema_200 and stays clear of it by at least the declared "
        "minimum separation."
    )
    PARAMETERS = (
        ParameterRequirement(
            name="min_separation",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            description=(
                "Smallest absolute ema_50/ema_200 gap that counts as a genuine cross "
                "rather than the two averages brushing against each other."
            ),
        ),
    )

    def assess(self, reading: AtomReading) -> AtomVerdict:
        minimum = reading.decimal_parameter("min_separation")
        latest = reading.latest
        earlier = reading.frames[-2]

        fast = latest.fact(_FAST)
        slow = latest.fact(_SLOW)
        separation = fast - slow
        magnitude = abs(separation)
        side = _side(latest)
        previous_side = _side(earlier)
        clear = magnitude >= minimum

        evidence: dict[str, Any] = {
            _FAST: fast,
            _SLOW: slow,
            "separation": separation,
            "minimum_separation": minimum,
            "side": side.value if side is not None else None,
            "previous_side": previous_side.value if previous_side is not None else None,
        }
        strength = proportion_beyond(magnitude - minimum, minimum)

        if reading.previous_is_live:
            held = reading.previous_direction
            if side is not held:
                return AtomVerdict(
                    holds=False,
                    direction=held,
                    strength=None,
                    evidence=evidence,
                    explanation=(
                        f"{_FAST} {fast} has crossed back to the other side of {_SLOW} "
                        f"{slow}"
                    ),
                    invalidation=(
                        "the moving averages crossed back; the matched cross is void"
                    ),
                )
            if not clear:
                return AtomVerdict(
                    holds=False,
                    direction=held,
                    strength=None,
                    evidence=evidence,
                    explanation=(
                        f"the {_FAST}/{_SLOW} separation has collapsed to {magnitude}, "
                        f"inside the declared minimum {minimum}"
                    ),
                    invalidation=(
                        "the separation collapsed inside the declared minimum; the "
                        "matched cross is void"
                    ),
                )
            return AtomVerdict(
                holds=True,
                direction=held,
                strength=strength,
                evidence=evidence,
                explanation=(
                    f"{_FAST} {fast} remains on the {held.value} side of {_SLOW} {slow} "
                    f"by {magnitude}, at or beyond the declared minimum {minimum}"
                ),
            )

        crossed = (
            side is not None and previous_side is not None and side is not previous_side
        )
        if crossed and clear:
            return AtomVerdict(
                holds=True,
                direction=side,
                strength=strength,
                evidence=evidence,
                explanation=(
                    f"{_FAST} moved from the {previous_side.value} side to the "
                    f"{side.value} side of {_SLOW} {slow}, separation {magnitude} at or "
                    f"beyond the declared minimum {minimum}"
                ),
            )
        if not clear:
            return AtomVerdict(
                holds=False,
                forming=True,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"{_FAST} {fast} and {_SLOW} {slow} have converged to {magnitude}, "
                    f"inside the declared minimum separation {minimum}; a cross is "
                    f"possible but not confirmed"
                ),
            )
        return AtomVerdict(
            holds=False,
            direction=Direction.NEUTRAL,
            evidence=evidence,
            explanation=(
                f"{_FAST} {fast} has stayed on the same side of {_SLOW} {slow}; no "
                f"cross occurred on this bar"
            ),
        )
