"""No-wick directional candle, over the canonical M15 fixture.

``docs/FIXTURES.md`` states frame 3 is a no-wick bearish candle
(open == high == 2410.00, close == low == 2406.50) and frame 12 a no-wick
bullish one (open == low == 2406.00, close == high == 2410.00). Both have a
body ratio of exactly 1 and wick ratios of exactly 0; every other frame in the
fixture carries a wick of at least 0.071 of its range, above the declared
``max_wick_ratio`` of 0.05.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import Direction, StrategyState
from helios.contracts.window import MarketFactWindow
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
    return build(package_for("no_wick_candle"))


def test_the_replay_matches_the_fixture_by_hand(atom, policy):
    results = replay(atom, "xau_usd_m15", policy)
    assert states(results) == [
        "DORMANT", "DORMANT", "DORMANT",
        "MATCHED",                        # frame 3: bearish, all body
        "INVALID",                        # frame 4: ordinary bar
        "DORMANT", "DORMANT", "DORMANT", "DORMANT", "DORMANT", "DORMANT", "DORMANT",
        "MATCHED",                        # frame 12: bullish, all body
        "INVALID",                        # frame 13
        "DORMANT", "DORMANT",
    ]


def test_the_bearish_bar_is_reported_with_its_geometry(atom, policy):
    envelope = at(replay(atom, "xau_usd_m15", policy), 3)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.SHORT
    assert envelope.evidence["body_ratio"] == Decimal("1")
    assert envelope.evidence["upper_wick_ratio"] == Decimal("0")
    assert envelope.evidence["lower_wick_ratio"] == Decimal("0")
    assert envelope.strength == Decimal("1")
    assert "the body points SHORT" in envelope.explanation


def test_the_bullish_bar_points_the_other_way(atom, policy):
    envelope = at(replay(atom, "xau_usd_m15", policy), 12)
    assert envelope.direction is Direction.LONG
    assert "the body points LONG" in envelope.explanation


def test_a_body_heavy_bar_with_a_wick_is_forming(policy):
    """Frame 6's body is 0.75 of its range but its wicks are 0.125 each.

    With the body threshold relaxed to 0.70 the body leg holds and the wick
    leg does not, which is exactly what FORMING means.
    """
    atom = build(
        package_variant(
            "no_wick_candle",
            **{"identity.strategy_version": "1.1.0",
               "parameters.min_body_ratio.value": Decimal("0.70")},
        )
    )
    envelope = at(replay(atom, "xau_usd_m15", policy), 6)
    assert envelope.state is StrategyState.FORMING
    assert envelope.direction is Direction.NEUTRAL
    assert envelope.evidence["body_ratio"] == Decimal("0.75")
    assert "exceeds the declared" in envelope.explanation


def test_a_bar_with_no_body_reports_no_direction(atom, policy):
    frame = make_frame(
        timeframe="M15",
        timestamp_utc="2026-06-01T00:00:00Z",
        open_="2400.00", high="2402.00", low="2398.00", close="2400.00",
    )
    context = context_for(
        atom, MarketFactWindow([frame]), policy,
        instrument=frame.instrument, evaluated_at_utc=frame.close_time_utc,
    )
    envelope = atom.evaluate(context)
    assert envelope.state is StrategyState.DORMANT
    assert envelope.direction is Direction.NEUTRAL
    assert "no body and no direction to report" in envelope.explanation
