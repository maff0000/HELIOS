"""Rejection wick, over the canonical M15 fixture.

``docs/FIXTURES.md`` states frame 5 is a bearish rejection wick (upper wick
5.50 of a 6.50 range, 0.846) and frame 9 a bullish one (lower wick 6.00 of a
7.00 range, 0.857), with every other frame under 0.60 — which is exactly the
declared ``min_wick_ratio``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import Direction, StrategyState
from helios.contracts.window import MarketFactWindow
from tests.conftest import make_frame
from tests.test_atom_support import at, build, context_for, package_for, replay, states


@pytest.fixture
def atom():
    return build(package_for("rejection_wick"))


def test_the_replay_matches_the_fixture_by_hand(atom, policy):
    """Two matching bars, each invalidated by the very next bar.

    The condition is a property of ONE bar, so it cannot persist: frames 6 and
    10 carry ordinary wicks and void the match, and frames 7 and 11 rearm.
    """
    results = replay(atom, "xau_usd_m15", policy)
    assert states(results) == [
        "DORMANT", "DORMANT", "DORMANT", "DORMANT", "DORMANT",  # frames 0-4
        "MATCHED",                                              # frame 5
        "INVALID",                                              # frame 6
        "DORMANT", "DORMANT",                                   # frames 7-8
        "MATCHED",                                              # frame 9
        "INVALID",                                              # frame 10
        "DORMANT", "DORMANT", "DORMANT", "DORMANT", "DORMANT",  # frames 11-15
    ]


def test_the_bearish_wick_is_reported_with_its_geometry(atom, policy):
    envelope = at(replay(atom, "xau_usd_m15", policy), 5)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.SHORT
    assert envelope.evidence["bar_range"] == Decimal("6.50")
    assert envelope.evidence["upper_wick"] == Decimal("5.50")
    assert envelope.evidence["upper_wick_ratio"] == Decimal("0.846154")
    assert envelope.evidence["lower_wick_ratio"] == Decimal("0.076923")
    assert envelope.strength == Decimal("0.846154")
    assert "upper wick 5.50 is 0.846154 of the bar's range 6.50" in envelope.explanation


def test_the_bullish_wick_is_reported_with_its_geometry(atom, policy):
    envelope = at(replay(atom, "xau_usd_m15", policy), 9)
    assert envelope.direction is Direction.LONG
    assert envelope.evidence["lower_wick"] == Decimal("6.00")
    assert envelope.evidence["lower_wick_ratio"] == Decimal("0.857143")
    assert envelope.strength == Decimal("0.857143")


def test_a_bar_just_under_the_threshold_stays_dormant(atom, policy):
    """Frame 8's upper wick is 0.80 of 1.50 — 0.533, under the declared 0.60."""
    envelope = at(replay(atom, "xau_usd_m15", policy), 8)
    assert envelope.state is StrategyState.DORMANT
    assert envelope.evidence["upper_wick_ratio"] == Decimal("0.533333")
    assert envelope.strength is None
    assert "neither wick dominates" in envelope.explanation


def test_a_bar_with_no_range_is_explained_rather_than_divided_by(atom, policy):
    frame = make_frame(
        timeframe="M15",
        timestamp_utc="2026-06-01T00:00:00Z",
        open_="2400.00", high="2400.00", low="2400.00", close="2400.00",
    )
    context = context_for(
        atom, MarketFactWindow([frame]), policy,
        instrument=frame.instrument, evaluated_at_utc=frame.close_time_utc,
    )
    envelope = atom.evaluate(context)
    assert envelope.state is StrategyState.DORMANT
    assert envelope.evidence["bar_range"] == Decimal("0")
    assert "will not divide by zero" in envelope.explanation
