"""Freshness and validity: stale or missing facts are never silently defaulted."""

from __future__ import annotations

from datetime import timedelta

import pytest

from helios.contracts import (
    FreshnessPolicy,
    Timeframe,
    assess_frame,
    require_fresh_frame,
    require_fresh_window,
)
from helios.errors import (
    ContractViolationError,
    FutureFactError,
    MissingFactError,
    StaleFactError,
)
from tests.conftest import make_frame, make_window, utc


def test_max_age_derives_from_the_timeframes_own_duration(policy):
    """One rule scales across timeframes; there is no per-timeframe table."""
    assert policy.max_age_for(Timeframe.M5) == timedelta(seconds=5 * 60 * 1.5 + 60)
    assert policy.max_age_for(Timeframe.H1) == timedelta(seconds=3600 * 1.5 + 60)
    assert policy.max_age_for(Timeframe.H4) == timedelta(seconds=14400 * 1.5 + 60)
    assert policy.max_age_for(Timeframe.H4) > policy.max_age_for(Timeframe.H1)


def test_a_configured_override_takes_precedence(policy):
    """The test configuration overrides D1 explicitly."""
    assert policy.max_age_for(Timeframe.D1) == timedelta(seconds=172800)


def test_a_recent_frame_is_fresh(policy):
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-05T04:01:00Z"))
    assert verdict.is_fresh
    assert verdict.age_seconds == 60
    assert verdict.max_age_seconds == 21660
    assert verdict.source == "hermes"
    assert verdict.schema_version == "hermes.market_fact/1.0.0"


def test_an_old_frame_is_stale_and_says_so(policy):
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-06T00:00:00Z"))
    assert not verdict.is_fresh
    assert verdict.age_seconds > verdict.max_age_seconds


def test_requiring_a_stale_frame_fails_loudly(policy):
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    with pytest.raises(StaleFactError) as caught:
        require_fresh_frame(frame, policy, now_utc=utc("2026-01-06T00:00:00Z"))
    context = caught.value.context
    assert context["age_seconds"] > context["max_age_seconds"]
    assert context["timeframe"] == "H4"


def test_a_missing_window_fails_loudly_rather_than_defaulting(policy):
    with pytest.raises(MissingFactError) as caught:
        require_fresh_window(None, policy, now_utc=utc("2026-01-05T04:01:00Z"), role="CONTEXT")
    assert caught.value.context["role"] == "CONTEXT"


def test_an_incomplete_bar_is_refused_when_policy_forbids_it(policy):
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z", complete=False)
    assert not policy.allow_incomplete_frames
    with pytest.raises(StaleFactError) as caught:
        require_fresh_frame(frame, policy, now_utc=utc("2026-01-05T04:01:00Z"))
    assert "incomplete" in str(caught.value)


def test_an_incomplete_bar_is_accepted_when_policy_permits_it():
    permissive = FreshnessPolicy(
        max_age_multiplier="1.5", grace=timedelta(seconds=60), allow_incomplete_frames=True,
        clock_skew_tolerance=timedelta(seconds=5)
    )
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z", complete=False)
    verdict = require_fresh_frame(frame, permissive, now_utc=utc("2026-01-05T04:01:00Z"))
    assert verdict.is_fresh
    assert not verdict.is_complete


# ------------------------------------------------- facts dated ahead of us
#
# A negative age has two causes that must not be conflated. The bar that has
# opened and not yet closed is legitimate and is covered by a stated rule. A
# fact dated arbitrarily far into the future is not, and clamping its age to
# zero published it as maximally fresh — which is exactly the silent zeroing
# docs/CONTRACTS.md says never happens.
#
# H4 fixtures, tolerance 5s: max_future = 4h + 5s = 14405s from the CLOSE
# instant, i.e. the bar may open up to 5s before our own clock reaches it.


def test_an_open_bar_reports_zero_age_by_a_stated_rule_not_a_clamp(policy):
    """The blessed case: evaluated at the H4 bar's OPEN, 4h before its close."""
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-05T00:00:00Z"))
    assert verdict.age_seconds == 0
    assert verdict.is_fresh
    assert not verdict.is_future_dated


def test_a_frame_at_the_exact_skew_tolerance_is_still_accepted(policy):
    """5s before the bar even opens: one whole bar plus the declared drift."""
    assert policy.clock_skew_tolerance == timedelta(seconds=5)
    assert policy.max_future_for(Timeframe.H4) == timedelta(hours=4, seconds=5)
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-04T23:59:55Z"))
    assert verdict.age_seconds == 0
    assert verdict.is_fresh
    assert not verdict.is_future_dated


def test_one_second_beyond_the_tolerance_is_refused_not_zeroed(policy):
    """The other side of the same boundary. One second decides it."""
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-04T23:59:54Z"))
    assert verdict.is_future_dated
    assert not verdict.is_fresh
    # The real, negative age is reported. A clamp here is the defect.
    assert verdict.age_seconds == -14406
    assert verdict.max_future_seconds == 14405


@pytest.mark.parametrize(
    "now_utc, expected_age_seconds",
    [
        ("2026-01-04T00:00:00Z", -100800),        # a day before the close
        ("1926-01-05T00:00:00Z", -3155774400),  # a century before the close
    ],
    ids=["a-day-early", "a-century-early"],
)
def test_a_wildly_future_dated_fact_is_never_maximally_fresh(
    policy, now_utc, expected_age_seconds
):
    """The reported defect, both magnitudes: age 0 / is_fresh True for both."""
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc(now_utc))
    assert verdict.is_future_dated
    assert not verdict.is_fresh
    assert verdict.age_seconds == expected_age_seconds


def test_requiring_a_future_dated_frame_fails_loudly(policy):
    """Loud like any other unusable fact, and named as its own failure mode."""
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    with pytest.raises(FutureFactError) as caught:
        require_fresh_frame(frame, policy, now_utc=utc("2026-01-04T00:00:00Z"))
    context = caught.value.context
    assert context["age_seconds"] == -100800
    assert context["max_future_seconds"] == 14405
    assert context["timeframe"] == "H4"
    assert context["evaluated_at_utc"] == "2026-01-04T00:00:00+00:00"


def test_a_future_dated_frame_is_refused_before_it_can_be_called_stale(policy):
    """FutureFactError, not StaleFactError: the concepts are opposites."""
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    with pytest.raises(FutureFactError):
        require_fresh_frame(frame, policy, now_utc=utc("2026-01-04T00:00:00Z"))
    assert not issubclass(FutureFactError, StaleFactError)


def test_the_tolerance_is_configured_and_scales_with_the_timeframe(policy):
    """One bar duration plus the declared drift; no per-timeframe table."""
    for timeframe in (Timeframe.M5, Timeframe.H1, Timeframe.H4, Timeframe.D1):
        assert policy.max_future_for(timeframe) == (
            timeframe.duration + policy.clock_skew_tolerance
        )


def test_window_freshness_judges_the_latest_frame(policy):
    window = make_window(4)
    verdict = require_fresh_window(window, policy, now_utc=utc("2026-01-05T16:01:00Z"))
    assert verdict.frame_timestamp_utc == window.latest_timestamp_utc


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_age_multiplier": "0"},
        {"max_age_multiplier": "-1"},
        {"max_age_multiplier": "not-a-number"},
        {"grace": timedelta(seconds=-1)},
        {"allow_incomplete_frames": "yes"},
        {"clock_skew_tolerance": timedelta(seconds=-1)},
        {"clock_skew_tolerance": 5},
        {"clock_skew_tolerance": "5"},
    ],
)
def test_malformed_policy_fails_loudly(kwargs):
    base = {
        "max_age_multiplier": "1.5",
        "grace": timedelta(seconds=60),
        "allow_incomplete_frames": False,
        "clock_skew_tolerance": timedelta(seconds=5),
    }
    with pytest.raises(ContractViolationError):
        FreshnessPolicy(**{**base, **kwargs})


def test_policy_is_immutable(policy):
    with pytest.raises(Exception):
        policy.max_age_multiplier = "9"  # type: ignore[misc]
    with pytest.raises(Exception):
        policy.overrides[Timeframe.H4] = timedelta(seconds=1)  # type: ignore[index]
