"""Swing proximity, over the canonical H1 fixture.

``docs/FIXTURES.md`` states the swing high is 2413.50 at frame 8, the swing
low 2392.50 at frame 15, and that the close returns to 2404.00 at frame 23,
within 9.50 of the swing high. With ``max_distance`` 10.00 that final bar is a
match; the 24-bar reference lookback means it is also the only bar with enough
history to evaluate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import Direction, StrategyState
from helios.errors import StrategySpecError
from tests.test_atom_support import (
    at,
    build,
    package_for,
    package_variant,
    replay,
    states,
)

#: A shorter lookback so the fixture yields a full lifecycle rather than one
#: evaluation. A different parameter set is a different definition.
TWELVE_BAR = {"identity.strategy_version": "1.1.0", "inputs.0.lookback": 12,
              "parameters.lookback_bars.value": 12}


def test_the_reference_lookback_matches_the_fixture_by_hand(policy):
    atom = build(package_for("swing_proximity"))
    results = replay(atom, "xau_usd_h1", policy)
    assert [index for index, _ in results] == [23]
    envelope = at(results, 23)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG
    assert envelope.evidence["swing_high"] == Decimal("2413.50")
    assert envelope.evidence["swing_low"] == Decimal("2392.50")
    assert envelope.evidence["distance_to_swing_high"] == Decimal("9.50")
    assert envelope.evidence["distance_to_swing_low"] == Decimal("11.50")
    assert envelope.evidence["swing_level"] == Decimal("2413.50")
    # 1 - 9.50/10.00
    assert envelope.strength == Decimal("0.05")
    assert "9.50 below the 24-bar swing high 2413.50" in envelope.explanation


def test_a_twelve_bar_lookback_walks_the_whole_state_model(policy):
    """One replay covering MATCHED, ACTIVE, WEAKENING, INVALID and a rearm.

    Frames 11-13 sit near the 2397.50 swing low and close in on it, so the
    location strengthens. Frame 14 closes at 2396.00, beyond that level, which
    invalidates the location the match described. After the rearm at frame 15,
    frames 16-21 drift away from the new 2392.50 low, so the location weakens
    on every bar, and frame 22 leaves the declared distance altogether.
    """
    atom = build(package_variant("swing_proximity", **TWELVE_BAR))
    results = replay(atom, "xau_usd_h1", policy)
    assert states(results) == [
        "MATCHED", "ACTIVE", "ACTIVE",          # frames 11-13
        "INVALID",                              # frame 14: closed beyond the low
        "DORMANT",                              # frame 15: rearmed
        "MATCHED",                              # frame 16
        "WEAKENING", "WEAKENING", "WEAKENING", "WEAKENING", "WEAKENING",
        "INVALID",                              # frame 22: past max_distance
        "DORMANT",                              # frame 23: rearmed
    ]


def test_weakening_tracks_a_falling_closeness(policy):
    atom = build(package_variant("swing_proximity", **TWELVE_BAR))
    results = replay(atom, "xau_usd_h1", policy)
    strengths = [
        at(results, index).strength for index in range(16, 22)
    ]
    assert strengths == [
        Decimal("0.7"), Decimal("0.55"), Decimal("0.4"),
        Decimal("0.25"), Decimal("0.15"), Decimal("0.05"),
    ]
    assert strengths == sorted(strengths, reverse=True)


def test_an_invalidated_location_names_the_level_it_passed(policy):
    atom = build(package_variant("swing_proximity", **TWELVE_BAR))
    envelope = at(replay(atom, "xau_usd_h1", policy), 14)
    assert envelope.state is StrategyState.INVALID
    # The direction of the occurrence that ended is preserved, so a consumer
    # can see WHAT was invalidated.
    assert envelope.direction is Direction.SHORT
    assert envelope.validity.reason is not None
    assert "moved beyond the swing level" in envelope.explanation


def test_an_equidistant_close_refuses_to_guess(policy):
    """Frame 21 is exactly 9.50 from both extremes of its 12-bar window."""
    atom = build(
        package_variant(
            "swing_proximity",
            **{"identity.strategy_version": "1.3.0", "inputs.0.lookback": 12,
               "parameters.lookback_bars.value": 12, "expiry.frames": 3},
        )
    )
    results = replay(atom, "xau_usd_h1", policy)
    envelope = at(results, 21)
    assert envelope.evidence["distance_to_swing_high"] == Decimal("9.50")
    assert envelope.evidence["distance_to_swing_low"] == Decimal("9.50")
    assert "ambiguous and this strategy will not guess" in envelope.explanation


def test_expiry_ends_an_occurrence_that_never_broke(policy):
    """With a three-frame expiry the frame-16 match ages out at frame 20."""
    atom = build(
        package_variant(
            "swing_proximity",
            **{"identity.strategy_version": "1.4.0", "inputs.0.lookback": 12,
               "parameters.lookback_bars.value": 12, "expiry.frames": 3},
        )
    )
    results = replay(atom, "xau_usd_h1", policy)
    assert states(results)[5:10] == [
        "MATCHED", "WEAKENING", "WEAKENING", "WEAKENING", "EXPIRED"
    ]
    expired = at(results, 20)
    assert expired.state is StrategyState.EXPIRED
    assert expired.direction is Direction.SHORT
    assert "aged past its declared validity window" in expired.explanation
    # Three H1 frames after the first match.
    assert (
        expired.validity.valid_until_utc - expired.first_matched_at_utc
    ).total_seconds() == 3 * 3600
    assert expired.last_evaluated_at_utc > expired.validity.valid_until_utc


def test_a_package_measuring_more_bars_than_it_declares_is_refused():
    with pytest.raises(StrategySpecError) as failure:
        build(
            package_variant(
                "swing_proximity",
                **{"identity.strategy_version": "1.5.0", "inputs.0.lookback": 4,
                   "parameters.lookback_bars.value": 24},
            )
        )
    assert failure.value.context["lookback_bars"] == 24
