"""The time-aligned scenario facts: one instrument, four timeframes, one clock.

The single-atom HERMES fixtures each prove one condition in isolation, and each
carries its own ``reference_now_utc`` — 2026-01-06T00:01Z, 2026-01-06T16:01Z,
2026-01-07T00:01Z. That is correct for what they are and fatal for what the PID
asks for next: **there is no instant at which all four are simultaneously
fresh**, so no multi-timeframe chain could ever be evaluated end to end over
real market-fact windows. This set exists to close that gap, and the first test
below states the gap rather than assuming the reader knows it.

Three properties are asserted here, and the second is the one that makes the set
*coherent* rather than merely simultaneous:

1. **One clock.** One instrument, one reference instant, one publication delay,
   and every timeframe fresh at that instant under the configured policy.
2. **One market.** M5 is the base truth and the coarser series are its exact
   aggregate wherever they overlap: same open, same high, same low, same close,
   same volume. Four documents describing four different markets would be four
   fixtures, not a scenario.
3. **Stated expectations are true.** Each document declares what it is there to
   prove, in its own ``expectations`` list, and the numbers behind those claims
   are checked here so a document and its description cannot drift apart.

Indicators are deliberately **not** aggregated. They are supplied HERMES facts
chosen for legibility, never recomputed by HELIOS — that is the whole point of
the input contract, and re-deriving them here would assert the opposite.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from helios.contracts.freshness import assess_frame
from helios.contracts.timeframe import Timeframe
from helios.hermes import load_fixture
from helios.runtime.feed import load_feed
from tests._scenario import SCENARIO_FEED_DIR

FACT_SCHEMA_VERSION = "hermes.market_fact/1.0.0"

#: The instant every document is judged against.
REFERENCE_INSTANT = "2026-03-02T16:01:00+00:00"

#: Which document supplies which timeframe.
DOCUMENTS = {
    "H4": "xau_usd_aligned_h4.json",
    "H1": "xau_usd_aligned_h1.json",
    "M15": "xau_usd_aligned_m15.json",
    "M5": "xau_usd_aligned_m5.json",
}


@pytest.fixture(scope="module")
def documents():
    return {
        code: load_fixture(SCENARIO_FEED_DIR / name) for code, name in DOCUMENTS.items()
    }


@pytest.fixture(scope="module")
def bars(documents):
    """Every document's frames, keyed by timeframe then bar-open instant."""
    return {
        code: {frame.timestamp_utc: frame for frame in document.frames}
        for code, document in documents.items()
    }


# ------------------------------------------------------ why this set exists


def test_the_single_atom_fixtures_could_not_have_served(repo_root):
    """The gap this set closes, stated rather than assumed.

    Each single-atom fixture is judged against its own instant, and no two of
    the four agree. Left alone that is correct — each proves one atom over one
    hand-checked series — but it means no single evaluation instant exists at
    which all four are fresh, and therefore no chain across them could be
    evaluated over real windows.
    """
    root = repo_root / "fixtures" / "hermes" / "xau_usd"
    instants = {
        name: load_fixture(root / f"xau_usd_{name}.json").reference_now_utc
        for name in ("h4", "h1", "m15", "m5")
    }
    assert len(set(instants.values())) > 1, instants


def test_the_single_atom_fixtures_are_untouched(repo_root):
    """Their exact contents are what the atom tests assert against."""
    root = repo_root / "fixtures" / "hermes" / "xau_usd"
    assert sorted(path.name for path in root.glob("*.json")) == [
        "xau_usd_h1.json",
        "xau_usd_h4.json",
        "xau_usd_h4_death_cross.json",
        "xau_usd_m15.json",
        "xau_usd_m5.json",
    ]


# ------------------------------------------------------------------ one clock


def test_every_document_describes_the_same_subject_at_the_same_instant(documents):
    assert {str(document.instrument) for document in documents.values()} == {"XAU_USD"}
    assert {
        document.reference_now_utc.isoformat() for document in documents.values()
    } == {REFERENCE_INSTANT}
    assert {document.fact_schema_version for document in documents.values()} == {
        FACT_SCHEMA_VERSION
    }


def test_every_document_delivers_its_facts_the_same_length_of_time_after_a_close(
    documents,
):
    """The feed's cadence is a fact of the data, never a constant in source."""
    delays = {
        code: document.reference_now_utc - document.window.latest.close_time_utc
        for code, document in documents.items()
    }
    assert set(delays.values()) == {timedelta(seconds=60)}, delays


def test_the_newest_bar_of_every_timeframe_closes_on_the_same_instant(documents):
    closes = {
        code: document.window.latest.close_time_utc for code, document in documents.items()
    }
    assert len(set(closes.values())) == 1, closes


def test_every_timeframe_is_fresh_at_the_reference_instant(documents, policy):
    """The property no combination of the single-atom fixtures has."""
    for code, document in documents.items():
        verdict = assess_frame(
            document.window.latest, policy, now_utc=document.reference_now_utc
        )
        assert verdict.is_fresh, (code, verdict.age_seconds, verdict.max_age_seconds)
        assert verdict.is_complete, code


def test_every_bar_is_complete_and_carries_coherent_provenance(documents):
    for code, document in documents.items():
        for frame in document.frames:
            assert frame.candle.complete, (code, frame.timestamp_utc)
            assert frame.provenance.observed_at_utc >= frame.close_time_utc, code
            assert frame.provenance.ingested_at_utc >= frame.provenance.observed_at_utc


# ----------------------------------------------------------------- one market


def _aggregate(frames):
    """Fold finer bars into the coarser bar they make up."""
    ordered = sorted(frames, key=lambda frame: frame.timestamp_utc)
    return {
        "open": ordered[0].fact("open"),
        "high": max(frame.fact("high") for frame in ordered),
        "low": min(frame.fact("low") for frame in ordered),
        "close": ordered[-1].fact("close"),
        "volume": sum((frame.candle.volume for frame in ordered), Decimal(0)),
    }


def _covered(finer, coarse_open, coarse: Timeframe, expected_count: int):
    """The finer bars falling inside one coarser bar, or None if incomplete."""
    span = coarse.duration
    inside = [
        frame
        for moment, frame in finer.items()
        if coarse_open <= moment < coarse_open + span
    ]
    return inside if len(inside) == expected_count else None


@pytest.mark.parametrize(
    "coarse_code,fine_code,ratio",
    [("M15", "M5", 3), ("H1", "M5", 12), ("H4", "H1", 4)],
    ids=["m15-from-m5", "h1-from-m5", "h4-from-h1"],
)
def test_the_coarser_series_are_the_exact_aggregate_of_the_finer(
    bars, coarse_code, fine_code, ratio
):
    """One market, told at four resolutions.

    Only fully covered bars are checked: the H1 series reaches back a day
    further than M5 does, and the oldest H4 bar predates H1. A partially
    covered bar is not evidence of incoherence, so it is skipped rather than
    fudged — and the count assertion below proves the check is not vacuous.
    """
    coarse = Timeframe.parse(coarse_code)
    checked = 0
    for moment, frame in sorted(bars[coarse_code].items()):
        inside = _covered(bars[fine_code], moment, coarse, ratio)
        if inside is None:
            continue
        checked += 1
        derived = _aggregate(inside)
        for field, value in derived.items():
            observed = (
                frame.candle.volume if field == "volume" else frame.fact(field)
            )
            assert observed == value, (coarse_code, moment, field, observed, value)
    assert checked >= 4, f"only {checked} {coarse_code} bars were covered by {fine_code}"


def test_the_series_span_what_the_scenario_needs(documents):
    counts = {code: len(document.frames) for code, document in documents.items()}
    assert counts == {"H4": 8, "H1": 29, "M15": 28, "M5": 84}, counts


# ------------------------------------------------------- stated expectations


def test_the_context_series_crosses_exactly_once_and_where_it_says(bars):
    """H4: ema_50 - ema_200 runs negative, then crosses on the 08:00 bar."""
    separations = [
        (moment, frame.fact("ema_50") - frame.fact("ema_200"))
        for moment, frame in sorted(bars["H4"].items())
    ]
    assert [str(value) for _, value in separations] == [
        "-6.00", "-5.00", "-4.00", "-3.00", "-2.50", "-1.00", "1.25", "2.00"
    ]
    crossings = [
        moment
        for (_, earlier), (moment, later) in zip(separations, separations[1:])
        if (earlier > 0) != (later > 0)
    ]
    assert [moment.isoformat() for moment in crossings] == [
        "2026-03-02T08:00:00+00:00"
    ]


def test_the_location_series_carries_the_swing_extremes_it_declares(bars):
    highs = {moment: frame.fact("high") for moment, frame in bars["H1"].items()}
    lows = {moment: frame.fact("low") for moment, frame in bars["H1"].items()}
    assert max(highs.values()) == Decimal("2416.00")
    assert min(lows.values()) == Decimal("2380.00")
    approach = [
        frame.fact("close")
        for moment, frame in sorted(bars["H1"].items())
        if moment.day == 2 and 12 <= moment.hour <= 15
    ]
    assert [str(value) for value in approach] == [
        "2408.00", "2411.50", "2413.00", "2414.00"
    ]
    # Each of those is inside the declared 10.00 of the swing high, and none
    # passes it — which is what keeps the location live rather than voided.
    for close in approach:
        assert Decimal(0) < Decimal("2416.00") - close <= Decimal("10.00")


def test_exactly_three_confirmation_bars_carry_a_dominant_wick(bars):
    dominant = {}
    for moment, frame in sorted(bars["M15"].items()):
        opened, high, low, close = (frame.fact(name) for name in ("open", "high", "low", "close"))
        span = high - low
        upper = (high - max(opened, close)) / span
        lower = (min(opened, close) - low) / span
        if max(upper, lower) >= Decimal("0.6"):
            dominant[moment.strftime("%H:%M")] = ("upper" if upper > lower else "lower")
    assert dominant == {"13:30": "lower", "13:45": "lower", "14:00": "lower"}


def test_the_trigger_series_breaks_its_declared_range_exactly_once(bars):
    """M5: the 13:55 bar clears the prior 19-bar range by more than the declared
    0.5 x atr_14, and no earlier bar does."""
    ordered = [frame for _, frame in sorted(bars["M5"].items())]
    breaks = []
    for index in range(19, len(ordered)):
        prior = ordered[index - 19 : index]
        latest = ordered[index]
        high = max(frame.fact("high") for frame in prior)
        low = min(frame.fact("low") for frame in prior)
        clearance = latest.fact("atr_14") * Decimal("0.5")
        if latest.fact("close") > high + clearance:
            breaks.append((latest.timestamp_utc.strftime("%H:%M"), "LONG", high))
        elif latest.fact("close") < low - clearance:
            breaks.append((latest.timestamp_utc.strftime("%H:%M"), "SHORT", low))
    assert breaks == [("13:55", "LONG", Decimal("2408.50"))]


def test_every_document_states_what_it_proves(documents):
    for code, document in documents.items():
        assert document.expectations, code
        assert document.description.strip(), code


# ------------------------------------------------------------ as a live feed


def test_the_set_loads_as_one_ordered_feed(config):
    feed = load_feed(
        SCENARIO_FEED_DIR,
        instrument="XAU_USD",
        accepted_schema_versions=config.accepted_hermes_schema_versions,
    )
    assert feed.timeframes == (
        Timeframe.parse("M5"),
        Timeframe.parse("M15"),
        Timeframe.parse("H1"),
        Timeframe.parse("H4"),
    )
    assert feed.finest is Timeframe.parse("M5")
    assert feed.publication_delay == timedelta(seconds=60)


def test_the_feed_never_shows_a_strategy_a_fact_from_its_own_future(config):
    feed = load_feed(
        SCENARIO_FEED_DIR,
        instrument="XAU_USD",
        accepted_schema_versions=config.accepted_hermes_schema_versions,
    )
    for instant in feed.instants({Timeframe.parse("H1"): 24}):
        for timeframe, window in feed.windows_at(instant).items():
            assert window.latest.close_time_utc + feed.publication_delay <= instant, (
                timeframe.code,
                instant,
            )
