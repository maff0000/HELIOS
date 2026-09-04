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
from helios.errors import ContractViolationError, MissingFactError, StaleFactError
from tests.conftest import make_frame, make_window, utc


def test_max_age_derives_from_the_timeframes_own_duration(policy):
    """One rule scales across timeframes; there is no per-timeframe table."""
    assert policy.max_age_for(Timeframe.M5) == timedelta(seconds=5 * 60 * 1.5 + 60)
    assert policy.max_age_for(Timeframe.H1) == timedelta(seconds=3600 * 1.5 + 60)
    assert policy.max_age_for(Timeframe.H4) == timedelta(seconds=14400 * 1.5 + 60)
    assert policy.max_age_for(Timeframe.H4) > policy.max_age_for(Timeframe.H1)


def test_configured_override_wins(policy):
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
        max_age_multiplier="1.5", grace=timedelta(seconds=60), allow_incomplete_frames=True
    )
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z", complete=False)
    verdict = require_fresh_frame(frame, permissive, now_utc=utc("2026-01-05T04:01:00Z"))
    assert verdict.is_fresh
    assert not verdict.is_complete


def test_a_frame_from_the_future_reports_zero_age_not_a_negative_one(policy):
    frame = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    verdict = assess_frame(frame, policy, now_utc=utc("2026-01-05T00:00:00Z"))
    assert verdict.age_seconds == 0
    assert verdict.is_fresh


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
    ],
)
def test_malformed_policy_fails_loudly(kwargs):
    base = {
        "max_age_multiplier": "1.5",
        "grace": timedelta(seconds=60),
        "allow_incomplete_frames": False,
    }
    with pytest.raises(ContractViolationError):
        FreshnessPolicy(**{**base, **kwargs})


def test_policy_is_immutable(policy):
    with pytest.raises(Exception):
        policy.max_age_multiplier = "9"  # type: ignore[misc]
    with pytest.raises(Exception):
        policy.overrides[Timeframe.H4] = timedelta(seconds=1)  # type: ignore[index]
