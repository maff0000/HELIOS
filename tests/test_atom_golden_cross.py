"""Golden / death cross, over the canonical H4 fixtures.

``docs/FIXTURES.md`` states what these fixtures contain: ``ema_50`` below
``ema_200`` for frames 0..5 and crossing above at frame 6 in ``xau_usd_h4``,
and a cross below at frame 4 in ``xau_usd_h4_death_cross``. Every expectation
below was derived from those stated facts and the declared
``min_separation`` of 0.25 by hand.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import Direction, StrategyState
from helios.errors import MissingFactError
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
from helios.contracts.window import MarketFactWindow


@pytest.fixture
def atom():
    return build(package_for("golden_cross"))


def _h4_series(day: str, fast_values: list[str], slow: str = "2400.00"):
    """Consecutive H4 bars carrying the given ema_50 readings.

    Bars must be one timeframe apart: a gap wide enough to cross the package's
    declared expiry would be testing expiry, not the condition.
    """
    return [
        make_frame(
            timestamp_utc=f"{day}T{4 * index:02d}:00:00Z",
            indicators={"ema_50": fast, "ema_200": slow},
        )
        for index, fast in enumerate(fast_values)
    ]


def test_the_golden_cross_replay_matches_the_fixture_by_hand(atom, policy):
    """Separations are -3.00, -2.00, -1.00, -0.50, -0.10, then +0.40 upward.

    Only the -0.10 bar sits inside the declared 0.25 minimum, so exactly one
    FORMING appears; the side change at frame 6 with 0.40 of separation is the
    match, and every later bar keeps the same side with a wider gap.
    """
    results = replay(atom, "xau_usd_h4", policy)
    assert states(results) == [
        "DORMANT", "DORMANT", "DORMANT", "DORMANT",  # frames 1-4
        "FORMING",                                    # frame 5: gap 0.10 < 0.25
        "MATCHED",                                    # frame 6: crossed by 0.40
        "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE",
    ]


def test_the_match_frame_reports_the_cross_it_saw(atom, policy):
    envelope = at(replay(atom, "xau_usd_h4", policy), 6)
    assert envelope.state is StrategyState.MATCHED
    assert envelope.direction is Direction.LONG
    assert envelope.evidence["ema_50"] == Decimal("2400.40")
    assert envelope.evidence["ema_200"] == Decimal("2400.00")
    assert envelope.evidence["separation"] == Decimal("0.40")
    assert envelope.evidence["previous_side"] == "SHORT"
    assert envelope.evidence["side"] == "LONG"
    # (0.40 - 0.25) / ((0.40 - 0.25) + 0.25)
    assert envelope.strength == Decimal("0.375")
    assert "moved from the SHORT side to the LONG side" in envelope.explanation
    assert "became true on this evaluation" in envelope.explanation


def test_the_forming_frame_explains_what_is_missing(atom, policy):
    envelope = at(replay(atom, "xau_usd_h4", policy), 5)
    assert envelope.state is StrategyState.FORMING
    assert envelope.direction is Direction.NEUTRAL
    assert envelope.strength is None
    assert "converged" in envelope.explanation
    assert "inside the declared minimum separation" in envelope.explanation


def test_the_death_cross_replay_matches_the_fixture_by_hand(atom, policy):
    """Separations run +2.00, +1.00, +0.50, then -0.40 downward."""
    results = replay(atom, "xau_usd_h4_death_cross", policy)
    assert states(results) == [
        "DORMANT", "DORMANT", "DORMANT",  # frames 1-3, same side throughout
        "MATCHED",                        # frame 4: crossed below by 0.40
        "ACTIVE", "ACTIVE", "ACTIVE",
    ]
    matched = at(results, 4)
    assert matched.direction is Direction.SHORT
    assert matched.evidence["separation"] == Decimal("-0.40")


def test_a_cold_start_does_not_claim_a_cross_it_never_saw(atom, policy):
    """The very first evaluation of an already-separated pair is DORMANT."""
    first = replay(atom, "xau_usd_h4", policy)[0][1]
    assert first.state is StrategyState.DORMANT
    assert first.evidence["separation"] == Decimal("-3.00")
    assert "no cross occurred on this bar" in first.explanation


def test_lifecycle_instants_describe_the_occurrence(atom, policy):
    results = replay(atom, "xau_usd_h4", policy)
    matched = at(results, 6)
    last = at(results, 11)
    assert matched.first_matched_at_utc == matched.last_evaluated_at_utc
    assert matched.active_since_utc == matched.first_matched_at_utc
    # One continuous activation: the origin instants never move while live.
    assert last.first_matched_at_utc == matched.first_matched_at_utc
    assert last.active_since_utc == matched.active_since_utc
    assert last.last_matched_at_utc == last.last_evaluated_at_utc
    # Six H4 frames of declared expiry from the first match.
    assert last.validity.valid_from_utc == matched.first_matched_at_utc
    assert (
        last.validity.valid_until_utc - matched.first_matched_at_utc
    ).total_seconds() == 6 * 4 * 3600


def test_a_cross_back_invalidates_and_then_rearms(policy):
    """A live LONG cross that reverses resolves through INVALID, then DORMANT."""
    package = package_variant(
        "golden_cross", **{"identity.strategy_version": "1.1.0"}
    )
    atom = build(package)
    frames = _h4_series(
        "2026-02-02",
        ["2399.00", "2401.00", "2402.00", "2399.00", "2398.00"],
    )
    observed = []
    previous = None
    for index in range(1, len(frames)):
        context = context_for(
            atom,
            MarketFactWindow(frames[: index + 1]),
            policy,
            instrument=frames[0].instrument,
            evaluated_at_utc=frames[index].close_time_utc,
            previous=previous,
        )
        previous = atom.evaluate(context)
        observed.append(previous.state.value)
    assert observed == ["MATCHED", "ACTIVE", "INVALID", "DORMANT"]


def test_weakening_is_published_when_the_separation_narrows(policy):
    """The package enables weakening, so a shrinking gap must report it."""
    atom = build(package_variant("golden_cross", **{"identity.strategy_version": "1.2.0"}))
    frames = _h4_series("2026-03-02", ["2399.00", "2402.00", "2401.00", "2400.60"])
    observed = []
    previous = None
    for index in range(1, len(frames)):
        context = context_for(
            atom,
            MarketFactWindow(frames[: index + 1]),
            policy,
            instrument=frames[0].instrument,
            evaluated_at_utc=frames[index].close_time_utc,
            previous=previous,
        )
        previous = atom.evaluate(context)
        observed.append((previous.state.value, previous.strength))
    assert [state for state, _ in observed] == ["MATCHED", "WEAKENING", "WEAKENING"]
    strengths = [strength for _, strength in observed]
    assert strengths[0] > strengths[1] > strengths[2]


def test_a_missing_indicator_fails_loudly_and_is_never_defaulted(atom, policy):
    """An absent ema_200 must raise, not read as zero."""
    frames = [
        make_frame(
            timestamp_utc="2026-04-01T00:00:00Z",
            indicators={"ema_50": "2401.00", "ema_200": "2400.00"},
        ),
        make_frame(timestamp_utc="2026-04-02T00:00:00Z", indicators={"ema_50": "2402.00"}),
    ]
    context = context_for(
        atom,
        MarketFactWindow(frames),
        policy,
        instrument=frames[0].instrument,
        evaluated_at_utc=frames[-1].close_time_utc,
    )
    with pytest.raises(MissingFactError) as failure:
        atom.evaluate(context)
    assert failure.value.context["field"] == "ema_200"
