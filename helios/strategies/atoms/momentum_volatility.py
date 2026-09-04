"""Momentum reaching an extreme while volatility expands.

Both legs are HERMES facts consumed as given: ``rsi_14`` is the momentum
reading and ``atr_14`` the volatility reading. HELIOS computes neither. The
only arithmetic here is the ratio of this bar's ``atr_14`` to the previous
bar's — strategy-local geometry over two supplied facts, in the same sense
that a wick ratio is geometry over a supplied candle.

The two legs are deliberately separable, which is what gives this atom a real
FORMING phase: momentum at an extreme while volatility is flat is a declared
precondition that holds without the full condition holding.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Optional

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

_MOMENTUM_CEILING = Decimal(100)


class MomentumVolatilityAtom(AtomicStrategy):
    """``rsi_14`` at a declared extreme while ``atr_14`` expands by a declared factor."""

    ATOM_NAME = "momentum_volatility"
    REQUIRED_FIELDS = ("close", "rsi_14", "atr_14")
    MIN_LOOKBACK = 2
    DIRECTIONAL = True
    SUMMARY = (
        "rsi_14 reaches a declared extreme on the same bar that atr_14 expands by at "
        "least the declared factor."
    )
    PARAMETERS = (
        ParameterRequirement(
            name="momentum_upper",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            maximum=_MOMENTUM_CEILING,
            description="rsi_14 at or above this reads as upward momentum.",
        ),
        ParameterRequirement(
            name="momentum_lower",
            type=ParameterType.DECIMAL,
            minimum=Decimal(0),
            maximum=_MOMENTUM_CEILING,
            description="rsi_14 at or below this reads as downward momentum.",
        ),
        ParameterRequirement(
            name="min_volatility_expansion",
            type=ParameterType.DECIMAL,
            minimum=Decimal(1),
            description=(
                "Smallest ratio of this bar's atr_14 to the previous bar's that counts "
                "as expanding volatility."
            ),
        ),
    )

    @classmethod
    def validate_binding(
        cls, package: StrategyPackage, parameters: Mapping[str, Any]
    ) -> None:
        super().validate_binding(package, parameters)
        lower = parameters["momentum_lower"]
        upper = parameters["momentum_upper"]
        if lower >= upper:
            raise StrategySpecError(
                "the declared momentum bounds overlap; one reading cannot be both "
                "extremes and HELIOS will not decide which was meant",
                strategy_id=str(package.identity.strategy_id),
                momentum_lower=str(lower),
                momentum_upper=str(upper),
            )

    @staticmethod
    def _momentum_strength(
        momentum: Direction, reading_value: Decimal, lower: Decimal, upper: Decimal
    ) -> Decimal:
        """How far past the declared extreme the momentum reading sits, as 0..1."""
        if momentum is Direction.LONG:
            headroom = _MOMENTUM_CEILING - upper
            if headroom <= 0:
                return Decimal(1)
            return unit_interval(quantised_ratio(reading_value - upper, headroom))
        if lower <= 0:
            return Decimal(1)
        return unit_interval(quantised_ratio(lower - reading_value, lower))

    def assess(self, reading: AtomReading) -> AtomVerdict:
        upper = reading.decimal_parameter("momentum_upper")
        lower = reading.decimal_parameter("momentum_lower")
        minimum_expansion = reading.decimal_parameter("min_volatility_expansion")

        latest = reading.latest
        earlier = reading.frames[-2]
        momentum_reading = latest.fact("rsi_14")
        volatility = latest.fact("atr_14")
        earlier_volatility = earlier.fact("atr_14")

        evidence: dict[str, Any] = {
            "rsi_14": momentum_reading,
            "atr_14": volatility,
            "previous_atr_14": earlier_volatility,
            "momentum_upper": upper,
            "momentum_lower": lower,
            "min_volatility_expansion": minimum_expansion,
            "volatility_expansion": None,
        }

        if earlier_volatility == 0:
            return AtomVerdict(
                holds=False,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    "the previous bar reports no volatility, so an expansion ratio "
                    "cannot be formed; this strategy will not divide by zero to "
                    "produce a state"
                ),
            )

        expansion = quantised_ratio(volatility, earlier_volatility)
        evidence["volatility_expansion"] = expansion
        expanding = expansion >= minimum_expansion

        momentum: Optional[Direction] = None
        if momentum_reading >= upper:
            momentum = Direction.LONG
        elif momentum_reading <= lower:
            momentum = Direction.SHORT

        if momentum is not None and expanding:
            strength = self._momentum_strength(momentum, momentum_reading, lower, upper)
            edge = upper if momentum is Direction.LONG else lower
            return AtomVerdict(
                holds=True,
                direction=momentum,
                strength=strength,
                evidence=evidence,
                explanation=(
                    f"rsi_14 {momentum_reading} reached the declared {momentum.value} "
                    f"extreme {edge} while atr_14 expanded {expansion}x from "
                    f"{earlier_volatility} to {volatility}, at or beyond the declared "
                    f"{minimum_expansion}x"
                ),
            )
        if momentum is not None:
            edge = upper if momentum is Direction.LONG else lower
            return AtomVerdict(
                holds=False,
                forming=True,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"rsi_14 {momentum_reading} reached the declared {momentum.value} "
                    f"extreme {edge} but atr_14 expanded only {expansion}x, under the "
                    f"declared {minimum_expansion}x"
                ),
            )
        if expanding:
            return AtomVerdict(
                holds=False,
                forming=True,
                direction=Direction.NEUTRAL,
                evidence=evidence,
                explanation=(
                    f"atr_14 expanded {expansion}x, at or beyond the declared "
                    f"{minimum_expansion}x, but rsi_14 {momentum_reading} is between "
                    f"the declared extremes {lower} and {upper}"
                ),
            )
        return AtomVerdict(
            holds=False,
            direction=Direction.NEUTRAL,
            evidence=evidence,
            explanation=(
                f"rsi_14 {momentum_reading} is between the declared extremes {lower} "
                f"and {upper} and atr_14 expanded only {expansion}x"
            ),
        )
