"""The canonical HERMES fixtures, including the deliberately broken ones."""

from __future__ import annotations

from datetime import timedelta

from decimal import Decimal

import pytest

from helios.contracts import FreshnessPolicy, Timeframe, require_fresh_frame
from helios.errors import ContractViolationError, HeliosError, StaleFactError
from helios.hermes import load_fixture, read_fixture_document
from helios.hermes.fixture_source import FIXTURE_DOCUMENT_KEYS

CANONICAL = {
    "xau_usd_h4.json": ("H4", 12),
    "xau_usd_h4_death_cross.json": ("H4", 8),
    "xau_usd_h1.json": ("H1", 24),
    "xau_usd_m15.json": ("M15", 16),
    "xau_usd_m5.json": ("M5", 24),
}


@pytest.fixture(scope="module")
def canonical_dir(request):
    return request.config.rootpath / "fixtures" / "hermes" / "xau_usd"


@pytest.fixture(scope="module")
def malformed_dir(request):
    return request.config.rootpath / "fixtures" / "hermes" / "malformed"


@pytest.fixture(scope="module")
def stale_dir(request):
    return request.config.rootpath / "fixtures" / "hermes" / "stale"


@pytest.mark.parametrize(("name", "expected"), sorted(CANONICAL.items()))
def test_every_canonical_fixture_loads(canonical_dir, name, expected):
    timeframe, count = expected
    fixture = load_fixture(canonical_dir / name)
    assert fixture.timeframe is Timeframe.parse(timeframe)
    assert len(fixture.window) == count
    assert str(fixture.instrument) == "XAU_USD"
    assert fixture.expectations, "a fixture must document what it proves"


def test_the_gold_multi_timeframe_set_is_complete(canonical_dir):
    """4H / 1H / 15M / 5M, as the PID's GOLD template needs."""
    covered = {load_fixture(canonical_dir / name).timeframe for name in CANONICAL}
    assert {Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5} <= covered


def test_every_canonical_fixture_is_fresh_at_its_reference_instant(canonical_dir, policy):
    for name in CANONICAL:
        fixture = load_fixture(canonical_dir / name)
        verdict = require_fresh_frame(
            fixture.window.latest, policy, now_utc=fixture.reference_now_utc
        )
        assert verdict.is_fresh
        assert verdict.age_seconds == 60


def test_the_declared_schema_version_is_checked(canonical_dir):
    path = canonical_dir / "xau_usd_h4.json"
    load_fixture(path, accepted_schema_versions=["hermes.market_fact/1.0.0"])
    with pytest.raises(ContractViolationError) as caught:
        load_fixture(path, accepted_schema_versions=["hermes.market_fact/2.0.0"])
    assert "not configured to accept" in str(caught.value)


# ------------------------------------------------- what the fixtures must prove


def test_h4_fixture_contains_a_golden_cross(canonical_dir):
    frames = load_fixture(canonical_dir / "xau_usd_h4.json").window.frames
    below = [
        index
        for index, frame in enumerate(frames)
        if frame.fact("ema_50") < frame.fact("ema_200")
    ]
    above = [
        index
        for index, frame in enumerate(frames)
        if frame.fact("ema_50") > frame.fact("ema_200")
    ]
    assert below == list(range(0, 6))
    assert above == list(range(6, 12))


def test_h4_death_cross_fixture_crosses_the_other_way(canonical_dir):
    frames = load_fixture(canonical_dir / "xau_usd_h4_death_cross.json").window.frames
    assert frames[3].fact("ema_50") > frames[3].fact("ema_200")
    assert frames[4].fact("ema_50") < frames[4].fact("ema_200")


def test_h1_fixture_contains_a_clear_swing_high_and_low(canonical_dir):
    frames = load_fixture(canonical_dir / "xau_usd_h1.json").window.frames
    highs = [frame.fact("high") for frame in frames]
    lows = [frame.fact("low") for frame in frames]
    assert highs.index(max(highs)) == 8
    assert lows.index(min(lows)) == 15
    assert max(highs) == Decimal("2413.50")
    assert min(lows) == Decimal("2392.50")


def test_m15_fixture_contains_rejection_wicks_and_no_wick_candles(canonical_dir):
    frames = load_fixture(canonical_dir / "xau_usd_m15.json").window.frames

    def upper_wick_ratio(frame):
        candle = frame.candle
        return (candle.high - max(candle.open, candle.close)) / candle.range

    def lower_wick_ratio(frame):
        candle = frame.candle
        return (min(candle.open, candle.close) - candle.low) / candle.range

    assert upper_wick_ratio(frames[5]) > Decimal("0.6")
    assert lower_wick_ratio(frames[9]) > Decimal("0.6")
    # No-wick candles: the body is the whole range.
    assert frames[12].candle.open == frames[12].candle.low
    assert frames[12].candle.close == frames[12].candle.high
    assert frames[3].candle.open == frames[3].candle.high
    assert frames[3].candle.close == frames[3].candle.low
    ordinary = [
        index
        for index in range(len(frames))
        if index not in (3, 5, 9, 12)
    ]
    for index in ordinary:
        assert upper_wick_ratio(frames[index]) < Decimal("0.6")
        assert lower_wick_ratio(frames[index]) < Decimal("0.6")


def test_m5_fixture_contains_a_breakout_with_momentum_and_volatility(canonical_dir):
    frames = load_fixture(canonical_dir / "xau_usd_m5.json").window.frames
    range_high = max(frame.fact("high") for frame in frames[:18])
    assert range_high == Decimal("2410.00")
    assert frames[18].fact("close") > range_high
    assert frames[17].fact("rsi_14") < Decimal("70") < frames[18].fact("rsi_14")
    assert frames[18].fact("atr_14") == frames[17].fact("atr_14") * 2


def test_fixtures_form_an_ordered_temporal_sequence(canonical_dir):
    for name, (timeframe, _count) in CANONICAL.items():
        frames = load_fixture(canonical_dir / name).window.frames
        step = Timeframe.parse(timeframe).duration
        for previous, current in zip(frames, frames[1:]):
            assert current.timestamp_utc - previous.timestamp_utc == step


# ------------------------------------------------------------------ negatives


MALFORMED_CASES = {
    "missing_candle_field.json": "malformed Candle",
    "high_below_low.json": "high is below low",
    "unknown_candle_field.json": "malformed Candle",
    "naive_timestamp.json": "does not assume a timezone",
    "misaligned_timestamp.json": "plausible bar-open instant",
    "duplicate_timestamp.json": "duplicate timestamp",
    "out_of_order.json": "ascending time order",
    "mixed_instrument.json": "mixes instruments",
    "unsupported_timeframe.json": "unsupported timeframe",
    "negative_volume.json": "volume must not be negative",
    "rsi_out_of_range.json": "rsi_14 outside 0..100",
    "unknown_fixture_schema.json": "unknown fixture_schema_version",
    "ingested_before_observed.json": "ingested before observed",
    # The document format is closed. Both of these loaded silently while the
    # loader cherry-picked the keys it wanted instead of using a model.
    "unknown_document_key.json": "Extra inputs are not permitted",
    "unknown_frame_key.json": "Extra inputs are not permitted",
}


@pytest.mark.parametrize(("name", "expected"), sorted(MALFORMED_CASES.items()))
def test_malformed_fixtures_fail_loudly(malformed_dir, name, expected):
    with pytest.raises(HeliosError) as caught:
        load_fixture(malformed_dir / name)
    assert expected in str(caught.value)
    assert name in str(caught.value)


def test_every_malformed_fixture_is_covered(malformed_dir):
    on_disk = {path.name for path in malformed_dir.iterdir() if path.is_file()}
    assert on_disk == set(MALFORMED_CASES)


def test_a_stale_fixture_is_structurally_valid_but_refused(stale_dir, policy):
    """Staleness is a policy verdict, not a structural defect."""
    fixture = load_fixture(stale_dir / "xau_usd_h4_stale.json")
    assert len(fixture.window) == 12
    with pytest.raises(StaleFactError) as caught:
        require_fresh_frame(fixture.window.latest, policy, now_utc=fixture.reference_now_utc)
    assert caught.value.context["age_seconds"] > caught.value.context["max_age_seconds"]


def test_an_incomplete_last_bar_obeys_the_configured_policy(stale_dir, policy):
    fixture = load_fixture(stale_dir / "xau_usd_h4_incomplete_last_bar.json")
    assert not fixture.window.latest.candle.complete
    with pytest.raises(StaleFactError):
        require_fresh_frame(fixture.window.latest, policy, now_utc=fixture.reference_now_utc)
    permissive = FreshnessPolicy(
        max_age_multiplier=policy.max_age_multiplier,
        grace=policy.grace,
        allow_incomplete_frames=True,
        clock_skew_tolerance=timedelta(seconds=5),
    )
    verdict = require_fresh_frame(
        fixture.window.latest, permissive, now_utc=fixture.reference_now_utc
    )
    assert verdict.is_fresh and not verdict.is_complete


def test_fixture_documents_are_self_describing(canonical_dir):
    document = read_fixture_document(canonical_dir / "xau_usd_h4.json")
    for key in ("fixture_schema_version", "fixture_id", "description", "expectations",
                "instrument", "timeframe", "fact_schema_version", "source",
                "reference_now_utc", "frames"):
        assert key in document
    # The documented shape and the model enforcing it are one thing.
    assert set(document) == set(FIXTURE_DOCUMENT_KEYS)


def test_the_document_format_is_closed_at_both_levels(canonical_dir, tmp_path):
    """An unrecognised key is refused wherever it sits, and names the file.

    A cherry-picking loader accepted ``timestamp_utcc`` beside the real key and
    reported nothing, so an author who mistyped a key they meant to add got a
    clean load and a fixture that did not say what they thought it said.
    """
    import json as _json

    original = _json.loads(
        (canonical_dir / "xau_usd_h4.json").read_text(encoding="utf-8")
    )
    for label, mutate in (
        ("document", lambda d: d.update({"reference_nowe_utc": "2026-01-07T00:01:00Z"})),
        ("frame", lambda d: d["frames"][0].update({"timestamp_utcc": "2026-01-05T00:00:00Z"})),
    ):
        document = _json.loads(_json.dumps(original))
        mutate(document)
        path = tmp_path / f"{label}.json"
        path.write_text(_json.dumps(document), encoding="utf-8")
        with pytest.raises(ContractViolationError) as caught:
            read_fixture_document(path)
        problems = caught.value.context["problems"]
        assert any(item["type"] == "extra_forbidden" for item in problems), label
        assert caught.value.context["origin"] == str(path), label


def test_no_fixture_contains_credentials(request):
    """Fixtures are public market facts; nothing here may look like a secret."""
    root = request.config.rootpath / "fixtures"
    banned = ("password", "secret", "api_key", "apikey", "token", "credential",
              "connection_string", "passwd", "private_key")
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8").lower()
            for word in banned:
                assert word not in text, f"{path} mentions {word}"
