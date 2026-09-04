"""Canonical JSON: deterministic, exact, self-describing."""

from __future__ import annotations

import decimal
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


# ------------------------------------------- the installed decimal context

#: Contexts a host application could plausibly have installed. Each one changes
#: what ``Decimal.normalize()`` returns, which is why canonical serialisation
#: must not use an operation at all.
HOSTILE_CONTEXTS = (
    decimal.Context(prec=4),
    decimal.Context(prec=1),
    decimal.Context(prec=200),
    decimal.Context(prec=6, rounding=decimal.ROUND_FLOOR),
    decimal.Context(prec=9, rounding=decimal.ROUND_CEILING),
)

#: Values chosen to exercise every branch: trailing fractional zeros, integer
#: zeros that are significant, signs, zero in several spellings, exponent
#: notation, and a coefficient far longer than any plausible context precision.
DECIMAL_SAMPLES = (
    Decimal("0.846153846153846153846153846153846"),
    Decimal("5.50") / Decimal(1),
    Decimal("2400.00"),
    Decimal("1.50"),
    Decimal("-3.10"),
    Decimal("0.000"),
    Decimal("-0"),
    Decimal("1E+2"),
    Decimal("1E-30"),
    Decimal("123456789012345678901234567890.1230"),
)


def test_a_hostile_decimal_context_cannot_change_the_emitted_digits():
    """Published bytes must depend on the value alone, never on ambient state.

    ``Decimal.normalize()`` would fail this: it is an operation, so a
    ``prec=4`` context rounds ``0.846153...`` to ``0.8462`` while the default
    context emits ``0.846154``. Whatever context a host application installs,
    FALCON must receive the same bytes.
    """
    baseline = [canonical_decimal(value) for value in DECIMAL_SAMPLES]
    assert baseline[0] == "0.846153846153846153846153846153846"
    for context in HOSTILE_CONTEXTS:
        with decimal.localcontext(context):
            assert [canonical_decimal(value) for value in DECIMAL_SAMPLES] == baseline


def test_a_hostile_decimal_context_cannot_change_canonical_json():
    payload = {"strength": Decimal("0.846153846153846153846153846153846"),
               "close": Decimal("2400.00"),
               "nested": [Decimal("1.50"), {"atr": Decimal("0.000")}]}
    baseline = canonical_dumps(payload)
    assert "0.846153846153846153846153846153846" in baseline
    for context in HOSTILE_CONTEXTS:
        with decimal.localcontext(context):
            assert canonical_dumps(payload) == baseline


def test_the_guard_would_actually_catch_the_defect_it_was_written_for():
    """Proof the hostile contexts are genuinely hostile, not decorative."""
    value = Decimal("0.846153846153846153846153846153846")
    with decimal.localcontext(decimal.Context(prec=4)):
        assert format(value.normalize(), "f") == "0.8462"
        assert canonical_decimal(value) != format(value.normalize(), "f")
