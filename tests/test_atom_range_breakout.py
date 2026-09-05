"""Range breakout, over the canonical M5 fixture.

``docs/FIXTURES.md`` states the range high is exactly 2410.00 across frames
0..17 and that frame 18 breaks out with a close of 2413.50, while ``atr_14``
doubles from 1.20 to 2.40 on that bar. With ``min_atr_multiple`` 0.5 the
clearance on frame 18 is 1.20, so the level to beat is 2411.20 and 2413.50
beats it.
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

#: The reference package measures a 20-bar range, which on a 24-bar fixture
#: cannot be evaluated until after the breakout bar. This variant measures 19,
#: which is exactly the "frames 0..17 plus the bar being evaluated" the
#: fixture documents. A different parameter set is a different definition, so
#: it carries a different version.
NINETEEN_BAR = {"identity.strategy_version": "1.1.0", "inputs.0.lookback": 19,
                "parameters.lookback_bars.value": 19}


@pytest.fixture
def atom():
    return build(package_variant("range_breakout", **NINETEEN_BAR))


def test_the_breakout_replay_matches_the_fixture_by_hand(atom, policy):
    """Frame 18 clears the 2410.00 range high; every later close stays above it."""
    results = replay(atom, "xau_usd_m5", policy)
    assert [index for index, _ in results] == list(range(18, 24))
    assert states(results) == [
        "MATCHED", "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE"
    ]


def test_the_match_frame_reports_the_range_it_cleared(atom, policy):
    envelope = at(replay(atom, "xau_usd_m5", policy), 18)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG
    assert envelope.evidence["range_high"] == Decimal("2410.00")
    assert envelope.evidence["range_bars"] == 18
    assert envelope.evidence["close"] == Decimal("2413.50")
    assert envelope.evidence["atr_14"] == Decimal("2.40")
    assert envelope.evidence["clearance"] == Decimal("1.200")
    assert envelope.evidence["range_level"] == Decimal("2410.00")
    assert "cleared the 18-bar range high 2410.00" in envelope.explanation


def test_the_cleared_level_does_not_drift_with_the_range(atom, policy):
    """The remembered level stays put even as the recomputed range rises past it."""
    results = replay(atom, "xau_usd_m5", policy)
    later = at(results, 23)
    assert later.evidence["range_high"] == Decimal("2417.70")
    assert later.evidence["range_level"] == Decimal("2410.00")
    assert later.state is StrategyState.ACTIVE


def test_the_reference_package_reports_a_range_it_cannot_yet_clear(policy):
    """The 20-bar package can only be evaluated after the breakout bar.

    It publishes FORMING, because each bar trades through the recomputed range
    high without closing the declared clearance beyond it. That is the honest
    answer for that parameterisation, not a match found by shortening the
    range HELIOS was told to measure.
    """
    atom = build(package_for("range_breakout"))
    results = replay(atom, "xau_usd_m5", policy)
    assert [index for index, _ in results] == list(range(19, 24))
    assert set(states(results)) == {"FORMING"}
    assert "traded through the 19-bar range high" in at(results, 19).explanation


def test_a_close_back_inside_the_level_invalidates(atom, policy):
    """Frames engineered so the close falls back through the cleared level."""
    # Three flat bars set a range high of 2401.00; the fourth closes well
    # clear of it; the fifth closes back below the level it cleared.
    bars = [("2400.00", "2401.00", "2399.50", "2400.00")] * 3 + [
        ("2400.00", "2412.00", "2400.00", "2411.00"),
        ("2411.00", "2411.50", "2399.00", "2400.00"),
    ]
    frames = [
        make_frame(
            timeframe="M5",
            timestamp_utc=f"2026-05-01T00:{5 * index:02d}:00Z",
            open_=opened, high=high, low=low, close=close,
            indicators={"atr_14": "1.00"},
        )
        for index, (opened, high, low, close) in enumerate(bars)
    ]
    small = build(
        package_variant(
            "range_breakout",
            **{"identity.strategy_version": "1.2.0", "inputs.0.lookback": 4,
               "parameters.lookback_bars.value": 4},
        )
    )
    observed = []
    previous = None
    for index in range(3, len(frames)):
        context = context_for(
            small, MarketFactWindow(frames[: index + 1]), policy,
            instrument=frames[0].instrument,
            evaluated_at_utc=frames[index].close_time_utc,
            previous=previous,
        )
        previous = small.evaluate(context)
        observed.append(previous.state.value)
    assert observed == ["MATCHED", "INVALID"]
    assert "returned inside the level" in previous.explanation
    assert previous.direction is Direction.LONG


def test_a_package_measuring_more_bars_than_it_declares_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "range_breakout",
                **{"identity.strategy_version": "1.3.0", "inputs.0.lookback": 5,
                   "parameters.lookback_bars.value": 20},
            )
        )
    assert failure.value.context["declared_lookback"] == 5
    assert failure.value.context["lookback_bars"] == 20
