"""The HERMES-compatible input contract, and its loud failures."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from helios.contracts import (
    CANDLE_FIELDS,
    FACT_FIELDS,
    INDICATOR_FIELDS,
    Candle,
    IndicatorSet,
    Provenance,
)
from helios.errors import ContractViolationError, MissingFactError
from tests.conftest import make_frame


def test_contract_carries_every_hermes_field():
    """The field names must mirror the real HERMES fact schema."""
    assert set(CANDLE_FIELDS) == {
        "open", "high", "low", "close", "volume", "complete", "source",
    }
    assert set(INDICATOR_FIELDS) == {
        "rsi_14", "ema_9", "ema_21", "ema_50", "ema_200", "atr_14", "regime", "session",
    }
    assert set(FACT_FIELDS) == set(CANDLE_FIELDS) | set(INDICATOR_FIELDS)


def test_a_valid_frame_exposes_its_facts():
    frame = make_frame(indicators={"ema_50": "2401.50", "regime": "BULL_TREND"})
    assert frame.fact("close") == Decimal("2405.00")
    assert frame.fact("ema_50") == Decimal("2401.50")
    assert str(frame.fact("regime")) == "BULL_TREND"
    assert frame.close_time_utc == datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc)


def test_frames_are_immutable():
    frame = make_frame()
    with pytest.raises(Exception):
        frame.candle = None  # type: ignore[misc]
    with pytest.raises(Exception):
        frame.candle.close = Decimal("1")  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": "2390.00"},          # high below low
        {"low": "2500.00"},           # low above the open/close range
        {"close": "-2405.00"},        # negative price
        {"open_": "0"},               # zero price
        {"volume": "-1"},             # negative volume
    ],
)
def test_malformed_candles_fail_loudly(overrides):
    with pytest.raises(ContractViolationError):
        make_frame(**overrides)


def test_binary_floats_are_rejected_at_the_boundary():
    """Determinism: 0.1 + 0.2 != 0.3 in binary floating point."""
    with pytest.raises(ContractViolationError) as caught:
        Candle(
            open=2400.5, high="2410", low="2395", close="2405",
            volume="1", complete=True, source="hermes",
        )
    assert "float" in str(caught.value)


def test_unknown_upstream_field_fails_loudly():
    """A new HERMES field is a deliberate schema change, not a silent extra."""
    with pytest.raises(ContractViolationError):
        Candle(
            open="2400", high="2410", low="2395", close="2405", volume="1",
            complete=True, source="hermes", vwap="2402",
        )


def test_naive_timestamps_are_rejected():
    """Built directly, not through the helper: the contract itself must refuse
    a timestamp with no offset rather than assume a zone."""
    from helios.contracts import MarketFactFrame

    with pytest.raises(ContractViolationError) as caught:
        MarketFactFrame(
            instrument="XAU_USD",
            timeframe="H4",
            timestamp_utc="2026-01-05T00:00:00",
            candle=Candle(
                open="2400", high="2410", low="2395", close="2405",
                volume="1", complete=True, source="hermes",
            ),
            indicators=IndicatorSet(),
            provenance=Provenance(
                source="hermes",
                schema_version="hermes.market_fact/1.0.0",
                observed_at_utc="2026-01-05T04:00:01Z",
                ingested_at_utc="2026-01-05T04:00:02Z",
            ),
        )
    assert "timezone" in str(caught.value) or "offset" in str(caught.value)


def test_timestamp_must_fit_its_timeframe():
    with pytest.raises(ContractViolationError):
        make_frame(timeframe="M15", timestamp_utc="2026-01-05T00:07:00Z")


def test_absent_indicator_is_none_never_zero():
    indicators = IndicatorSet(ema_50="2400.00")
    assert indicators.ema_200 is None
    assert indicators.atr_14 is None
    assert indicators.available == {"ema_50"}


def test_requiring_an_absent_indicator_fails_loudly():
    frame = make_frame(indicators={"ema_50": "2400.00"})
    with pytest.raises(MissingFactError) as caught:
        frame.fact("ema_200")
    assert "ema_200" in str(caught.value)
    with pytest.raises(MissingFactError):
        frame.indicators.require("ema_50", "atr_14")


def test_requiring_an_unknown_field_fails_loudly():
    frame = make_frame()
    with pytest.raises(ContractViolationError):
        frame.fact("macd_hist")


@pytest.mark.parametrize("value", ["-1", "101"])
def test_rsi_outside_its_range_fails_loudly(value):
    with pytest.raises(ContractViolationError):
        IndicatorSet(rsi_14=value)


def test_regime_vocabulary_is_hermes_owned_but_shape_checked():
    """An unrecognised-but-well-formed regime is a fact HELIOS does not
    understand, not malformed input."""
    assert str(IndicatorSet(regime="SOME_NEW_REGIME").regime) == "SOME_NEW_REGIME"
    with pytest.raises(ContractViolationError):
        IndicatorSet(regime="bull trend")


def test_provenance_must_be_coherent():
    with pytest.raises(ContractViolationError):
        Provenance(
            source="hermes",
            schema_version="hermes.market_fact/1.0.0",
            observed_at_utc="2026-01-05T04:00:00Z",
            ingested_at_utc="2026-01-05T03:00:00Z",
        )
    with pytest.raises(ContractViolationError):
        Provenance(
            source="hermes",
            schema_version="not-qualified",
            observed_at_utc="2026-01-05T04:00:00Z",
            ingested_at_utc="2026-01-05T04:00:01Z",
        )


def test_helios_does_not_recompute_indicators():
    """HERMES owns indicators. HELIOS must expose no way to derive one."""
    import helios.contracts.market_fact as module

    banned = ("ema", "rsi", "atr", "sma", "macd", "indicator_from", "compute")
    for name in dir(module):
        if name.startswith("_"):
            continue
        attribute = getattr(module, name)
        if callable(attribute) and not isinstance(attribute, type):
            assert not any(token in name.lower() for token in banned), name
