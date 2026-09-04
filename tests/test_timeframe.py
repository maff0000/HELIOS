"""Timeframe vocabulary and the absence of a global role mapping."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from helios.contracts import ALL_TIMEFRAMES, Timeframe
from helios.errors import ContractViolationError


def test_every_required_timeframe_is_supported():
    assert {member.code for member in Timeframe} == {"M1", "M5", "M15", "H1", "H4", "D1"}


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("M1", timedelta(minutes=1)),
        ("M5", timedelta(minutes=5)),
        ("M15", timedelta(minutes=15)),
        ("H1", timedelta(hours=1)),
        ("H4", timedelta(hours=4)),
        ("D1", timedelta(days=1)),
    ],
)
def test_each_timeframe_knows_its_duration(code, expected):
    assert Timeframe.parse(code).duration == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4h", Timeframe.H4), ("H4", Timeframe.H4), ("15m", Timeframe.M15), ("d1", Timeframe.D1)],
)
def test_hermes_and_helios_codes_both_parse(text, expected):
    assert Timeframe.parse(text) is expected


@pytest.mark.parametrize("bad", ["H3", "", "4hours", "M0", 4, None])
def test_unsupported_timeframe_fails_loudly(bad):
    with pytest.raises(ContractViolationError):
        Timeframe.parse(bad)


def test_timeframes_order_by_duration():
    assert list(ALL_TIMEFRAMES) == [
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    ]
    assert Timeframe.M15 < Timeframe.H1 < Timeframe.H4


def test_sub_hour_alignment_is_checked():
    assert Timeframe.M15.is_aligned(datetime(2026, 1, 5, 12, 30, tzinfo=timezone.utc))
    assert not Timeframe.M15.is_aligned(datetime(2026, 1, 5, 12, 37, tzinfo=timezone.utc))
    assert not Timeframe.H1.is_aligned(datetime(2026, 1, 5, 12, 30, tzinfo=timezone.utc))
    assert not Timeframe.M5.is_aligned(datetime(2026, 1, 5, 12, 30, 15, tzinfo=timezone.utc))


def test_close_time_is_open_plus_duration():
    opened = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    assert Timeframe.H4.close_time(opened) == datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)


def test_no_global_semantic_role_mapping_exists():
    """The PID's CONTEXT/LOCATION/CONFIRMATION/TRIGGER template must never be a
    global constant. Roles bind to timeframes per strategy package only."""
    import helios.contracts.timeframe as module

    for name in dir(module):
        attribute = getattr(module, name)
        if isinstance(attribute, dict):
            keys = {str(key).upper() for key in attribute}
            assert not keys & {"CONTEXT", "LOCATION", "CONFIRMATION", "TRIGGER"}
    for member in Timeframe:
        assert not hasattr(member, "semantic_role")
        assert not hasattr(member, "role")
