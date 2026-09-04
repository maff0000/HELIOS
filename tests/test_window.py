"""Ordered, immutable lookback windows."""

from __future__ import annotations

import pytest

from helios.contracts import MarketFactWindow
from helios.errors import ContractViolationError, MissingFactError
from tests.conftest import make_frame, make_window


def test_window_preserves_order_and_identity(window):
    assert len(window) == 4
    assert str(window.instrument) == "XAU_USD"
    assert window.timeframe.code == "H4"
    timestamps = [frame.timestamp_utc for frame in window]
    assert timestamps == sorted(timestamps)
    assert window.latest is window[-1]
    assert window.oldest is window[0]


def test_lookback_returns_the_most_recent_frames_oldest_first(window):
    recent = window.lookback(2)
    assert len(recent) == 2
    assert recent[0].timestamp_utc < recent[1].timestamp_utc
    assert recent[-1] is window.latest


def test_insufficient_history_fails_loudly(window):
    """A strategy declaring 200 bars must never silently evaluate against 4."""
    with pytest.raises(MissingFactError) as caught:
        window.lookback(200)
    assert "insufficient history" in str(caught.value)


def test_previous_frame_is_addressable(window):
    assert window.previous(1) is window[-2]


def test_empty_window_is_refused():
    with pytest.raises(ContractViolationError):
        MarketFactWindow([])


def test_duplicate_timestamps_are_refused():
    frame = make_frame()
    with pytest.raises(ContractViolationError):
        MarketFactWindow([frame, frame])


def test_out_of_order_frames_are_refused_not_sorted():
    first = make_frame(timestamp_utc="2026-01-05T00:00:00Z")
    second = make_frame(timestamp_utc="2026-01-05T04:00:00Z")
    with pytest.raises(ContractViolationError) as caught:
        MarketFactWindow([second, first])
    assert "ascending" in str(caught.value)


def test_mixed_instruments_are_refused():
    with pytest.raises(ContractViolationError):
        MarketFactWindow(
            [
                make_frame(timestamp_utc="2026-01-05T00:00:00Z"),
                make_frame(instrument="EUR_USD", timestamp_utc="2026-01-05T04:00:00Z"),
            ]
        )


def test_mixed_timeframes_are_refused():
    with pytest.raises(ContractViolationError):
        MarketFactWindow(
            [
                make_frame(timeframe="H1", timestamp_utc="2026-01-05T00:00:00Z"),
                make_frame(timeframe="H4", timestamp_utc="2026-01-05T04:00:00Z"),
            ]
        )


def test_extending_a_window_leaves_the_original_untouched(window):
    successor = make_frame(timestamp_utc="2026-01-05T16:00:00Z")
    extended = window.extended_with(successor)
    assert len(window) == 4
    assert len(extended) == 5
    assert extended is not window
    trimmed = window.extended_with(successor, max_length=2)
    assert len(trimmed) == 2
    assert trimmed.latest is successor


def test_gaps_are_permitted_because_markets_close():
    frames = [
        make_frame(timestamp_utc="2026-01-05T00:00:00Z"),
        make_frame(timestamp_utc="2026-01-08T00:00:00Z"),
    ]
    assert len(MarketFactWindow(frames)) == 2


def test_completed_frames_are_explicitly_filtered():
    frames = [
        make_frame(timestamp_utc="2026-01-05T00:00:00Z"),
        make_frame(timestamp_utc="2026-01-05T04:00:00Z", complete=False),
    ]
    window = MarketFactWindow(frames)
    assert len(window.completed_frames()) == 1
