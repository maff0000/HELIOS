"""Momentum and volatility expansion, over the canonical M5 fixture.

``docs/FIXTURES.md`` states ``rsi_14`` crosses 70 at frame 18 (68 -> 72) and
``atr_14`` doubles from 1.20 to 2.40 on the same bar. The declared thresholds
are 70.00 / 30.00 with a minimum expansion of 1.50, so frame 18 satisfies both
legs and every later frame satisfies only the momentum one.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import Direction, StrategyState
from helios.contracts.window import MarketFactWindow
from helios.errors import StrategySpecError
from tests.conftest import make_frame
from tests.test_atom_support import (
    at,
    build,
    context_for,
    package_for,
    package_variant,
    replay,
    states,
)


@pytest.fixture
def atom():
    return build(package_for("momentum_volatility"))


def test_the_replay_matches_the_fixture_by_hand(atom, policy):
    """One match, voided the moment volatility stops expanding.

    atr_14 is flat at 1.20 for frames 0-17, so the expansion ratio is 1.0
    throughout and nothing forms. Frame 18 doubles it while rsi_14 reaches 72.
    From frame 19 onward atr_14 keeps rising but by well under the declared
    1.50x, so only the momentum leg holds.
    """
    results = replay(atom, "xau_usd_m5", policy)
    assert states(results) == [
        "DORMANT"] * 17 + [                      # frames 1-17
        "MATCHED",                               # frame 18: both legs
        "INVALID",                               # frame 19: volatility leg gone
        "DORMANT",                               # frame 20: rearmed
        "FORMING", "FORMING", "FORMING",         # frames 21-23: momentum only
    ]


def test_the_match_frame_reports_both_legs(atom, policy):
    envelope = at(replay(atom, "xau_usd_m5", policy), 18)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG
    assert envelope.evidence["rsi_14"] == Decimal("72.00")
    assert envelope.evidence["atr_14"] == Decimal("2.40")
    assert envelope.evidence["previous_atr_14"] == Decimal("1.20")
    assert envelope.evidence["volatility_expansion"] == Decimal("2")
    # (72 - 70) / (100 - 70)
    assert envelope.strength == Decimal("0.066667")
    assert "reached the declared LONG extreme 70.00" in envelope.explanation


def test_momentum_without_volatility_is_only_forming(atom, policy):
    envelope = at(replay(atom, "xau_usd_m5", policy), 21)
    assert envelope.state is StrategyState.FORMING
    assert envelope.direction is Direction.NEUTRAL
    assert envelope.strength is None
    assert "but atr_14 expanded only" in envelope.explanation


def test_the_downward_extreme_reports_short(policy):
    """rsi_14 at or below the lower threshold, with volatility expanding."""
    atom = build(package_for("momentum_volatility"))
    frames = [
        make_frame(
            timeframe="M5",
            timestamp_utc=f"2026-07-01T00:{5 * index:02d}:00Z",
            indicators={"rsi_14": rsi, "atr_14": atr},
        )
        for index, (rsi, atr) in enumerate(
            [("50.00", "1.00"), ("22.00", "2.00")]
        )
    ]
    context = context_for(
        atom, MarketFactWindow(frames), policy,
        instrument=frames[0].instrument, evaluated_at_utc=frames[-1].close_time_utc,
    )
    envelope = atom.evaluate(context)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.SHORT
    # (30 - 22) / 30
    assert envelope.strength == Decimal("0.266667")


def test_volatility_without_momentum_is_also_forming(policy):
    atom = build(package_for("momentum_volatility"))
    frames = [
        make_frame(
            timeframe="M5",
            timestamp_utc=f"2026-07-02T00:{5 * index:02d}:00Z",
            indicators={"rsi_14": rsi, "atr_14": atr},
        )
        for index, (rsi, atr) in enumerate([("50.00", "1.00"), ("55.00", "3.00")])
    ]
    context = context_for(
        atom, MarketFactWindow(frames), policy,
        instrument=frames[0].instrument, evaluated_at_utc=frames[-1].close_time_utc,
    )
    envelope = atom.evaluate(context)
    assert envelope.state is StrategyState.FORMING
    assert "between the declared extremes" in envelope.explanation


def test_a_flat_previous_reading_is_explained_rather_than_divided_by(policy):
    atom = build(package_for("momentum_volatility"))
    frames = [
        make_frame(
            timeframe="M5",
            timestamp_utc=f"2026-07-03T00:{5 * index:02d}:00Z",
            indicators={"rsi_14": "75.00", "atr_14": atr},
        )
        for index, atr in enumerate(["0", "2.00"])
    ]
    context = context_for(
        atom, MarketFactWindow(frames), policy,
        instrument=frames[0].instrument, evaluated_at_utc=frames[-1].close_time_utc,
    )
    envelope = atom.evaluate(context)
    assert envelope.state is StrategyState.DORMANT
    assert envelope.evidence["volatility_expansion"] is None
    assert "will not divide by zero" in envelope.explanation


def test_overlapping_momentum_bounds_are_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "momentum_volatility",
                **{"identity.strategy_version": "1.1.0",
                   "parameters.momentum_lower.value": Decimal("80.00")},
            )
        )
    assert failure.value.context["momentum_lower"] == "80.00"
