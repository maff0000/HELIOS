"""Canonical JSON: deterministic, exact, self-describing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from helios.contracts import Direction, StrategyState, Timeframe, canonical_dumps, canonical_loads
from helios.contracts._tokens import Instrument
from helios.contracts.serialisation import canonical_decimal, canonicalise
from helios.errors import SerialisationError


def test_object_keys_are_sorted_regardless_of_insertion_order():
    assert canonical_dumps({"b": 1, "a": 2}) == canonical_dumps({"a": 2, "b": 1})
    assert canonical_dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_sequences_keep_their_order():
    assert canonical_dumps(["b", "a"]) == '["b","a"]'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("2400.00"), "2400"),
        (Decimal("1.50"), "1.5"),
        (Decimal("0.000"), "0"),
        (Decimal("-3.10"), "-3.1"),
        (Decimal("0.1"), "0.1"),
    ],
)
def test_decimals_normalise_to_exact_text(value, expected):
    assert canonical_decimal(value) == expected


def test_decimal_text_survives_a_round_trip_numerically():
    for text in ("2400.00", "0.1", "1e-3", "123456789.123456789"):
        original = Decimal(text)
        restored = canonical_loads(canonical_dumps({"v": original}))["v"]
        assert Decimal(restored) == original


def test_binary_floats_are_refused():
    with pytest.raises(SerialisationError):
        canonical_dumps({"v": 0.1})


def test_non_finite_values_are_refused():
    with pytest.raises(SerialisationError):
        canonical_dumps({"v": Decimal("NaN")})
    with pytest.raises(SerialisationError):
        canonical_loads('{"v": NaN}')


def test_instants_are_fixed_width_utc():
    moment = datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)
    assert canonicalise(moment) == "2026-01-05T04:00:00.000000Z"


def test_non_utc_instants_are_converted_not_rejected():
    moment = datetime(2026, 1, 5, 6, 0, tzinfo=timezone(timedelta(hours=2)))
    assert canonicalise(moment) == "2026-01-05T04:00:00.000000Z"


def test_durations_must_be_published_as_explicit_seconds():
    with pytest.raises(SerialisationError):
        canonical_dumps({"age": timedelta(seconds=60)})


def test_enums_and_tokens_serialise_as_their_plain_codes():
    assert canonicalise(Timeframe.H4) == "H4"
    assert canonicalise(StrategyState.MATCHED) == "MATCHED"
    assert canonicalise(Direction.LONG) == "LONG"
    assert canonicalise(Instrument("XAU_USD")) == "XAU_USD"


def test_unknown_types_are_refused_rather_than_stringified():
    class Opaque:
        pass

    with pytest.raises(SerialisationError):
        canonical_dumps(Opaque())


def test_non_string_keys_are_refused():
    with pytest.raises(SerialisationError):
        canonical_dumps({1: "one"})


def test_output_has_no_insignificant_whitespace():
    text = canonical_dumps({"a": [1, 2], "b": {"c": 3}})
    assert text == '{"a":[1,2],"b":{"c":3}}'


def test_loading_requires_text():
    with pytest.raises(SerialisationError):
        canonical_loads(b"{}")  # type: ignore[arg-type]
