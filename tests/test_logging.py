"""Structured JSON logging, UTC only."""

from __future__ import annotations

import io
import json
import logging

import pytest

from helios.errors import HeliosError, StaleFactError
from helios.observability.logging import configure_logging, get_logger


@pytest.fixture
def captured():
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    yield stream
    logging.getLogger("helios").handlers.clear()


def lines(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_every_line_is_one_json_object(captured):
    get_logger("test").info("evaluated")
    record = lines(captured)[0]
    assert record["message"] == "evaluated"
    assert record["level"] == "INFO"
    assert record["logger"] == "helios.test"


def test_timestamps_are_utc(captured):
    get_logger("test").info("evaluated")
    assert lines(captured)[0]["ts_utc"].endswith("Z")


def test_no_local_time_appears_anywhere(captured):
    get_logger("test").info("evaluated", extra={"strategy_id": "golden_cross"})
    text = captured.getvalue()
    assert "+00:00" not in text
    assert text.count("Z") >= 1


def test_structured_fields_are_published(captured):
    get_logger("test").info(
        "state published",
        extra={"strategy_id": "golden_cross", "state": "MATCHED", "age_seconds": 60},
    )
    record = lines(captured)[0]
    assert record["strategy_id"] == "golden_cross"
    assert record["state"] == "MATCHED"
    assert record["age_seconds"] == 60


def test_helios_error_context_is_machine_readable(captured):
    try:
        raise StaleFactError("stale", timeframe="H4", age_seconds=99, max_age_seconds=60)
    except StaleFactError:
        get_logger("test").error("input refused", exc_info=True)
    record = lines(captured)[0]
    assert record["error_type"] == "StaleFactError"
    assert record["error_context"] == {
        "timeframe": "H4", "age_seconds": 99, "max_age_seconds": 60
    }
    assert "traceback" in record


def test_keys_are_sorted_for_stable_diffing(captured):
    get_logger("test").info("m", extra={"zebra": 1, "alpha": 2})
    text = captured.getvalue().strip()
    assert text.index('"alpha"') < text.index('"zebra"')


def test_decimals_and_datetimes_never_break_a_log_line(captured):
    from datetime import datetime, timezone
    from decimal import Decimal

    get_logger("test").info(
        "values",
        extra={
            "price": Decimal("2400.50"),
            "moment": datetime(2026, 1, 5, tzinfo=timezone.utc),
            "nested": {"a": [1, 2]},
        },
    )
    record = lines(captured)[0]
    assert record["price"] == "2400.50"
    assert record["moment"] == "2026-01-05T00:00:00Z"
    assert record["nested"] == {"a": [1, 2]}


def test_an_unknown_level_fails_loudly():
    with pytest.raises(HeliosError):
        configure_logging("CHATTY")


def test_configuring_twice_does_not_duplicate_output(captured):
    configure_logging("DEBUG", stream=captured)
    get_logger("test").info("once")
    assert len(lines(captured)) == 1
