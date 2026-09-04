"""Shared test fixtures.

Note what is NOT here: no configuration defaults. The suite loads its
configuration from ``tests/data/helios.test.toml`` exactly as a deployment
loads its own, because HELIOS has no configuration baked into source to fall
back on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from helios.clock import from_iso8601_utc
from helios.config import HeliosConfig, load_config
from helios.contracts import (
    Candle,
    FreshnessPolicy,
    IndicatorSet,
    MarketFactFrame,
    MarketFactWindow,
    Provenance,
    Timeframe,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_CONFIG_FILE = REPO_ROOT / "tests" / "data" / "helios.test.toml"
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "hermes"
PACKAGE_ROOT = REPO_ROOT / "fixtures" / "strategy_packages"

FACT_SCHEMA_VERSION = "hermes.market_fact/1.0.0"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture(scope="session")
def package_root() -> Path:
    return PACKAGE_ROOT


@pytest.fixture(scope="session")
def config() -> HeliosConfig:
    return load_config({}, config_file=TEST_CONFIG_FILE)


@pytest.fixture(scope="session")
def policy(config: HeliosConfig) -> FreshnessPolicy:
    return config.freshness_policy()


def utc(text: str) -> datetime:
    """Strict UTC parse. The helper must not paper over what the contract rejects."""
    return from_iso8601_utc(text)


def make_frame(
    *,
    instrument: str = "XAU_USD",
    timeframe: str = "H4",
    timestamp_utc: str = "2026-01-05T00:00:00Z",
    open_: str = "2400.00",
    high: str = "2410.00",
    low: str = "2395.00",
    close: str = "2405.00",
    volume: str = "1000",
    complete: bool = True,
    indicators: dict | None = None,
    observed_offset_seconds: int = 1,
) -> MarketFactFrame:
    """Build one valid frame; every field is explicit and overridable."""
    opened = utc(timestamp_utc)
    closed = opened + Timeframe.parse(timeframe).duration
    return MarketFactFrame(
        instrument=instrument,
        timeframe=timeframe,
        timestamp_utc=opened,
        candle=Candle(
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            complete=complete,
            source="hermes",
        ),
        indicators=IndicatorSet(**(indicators or {})),
        provenance=Provenance(
            source="hermes",
            schema_version=FACT_SCHEMA_VERSION,
            observed_at_utc=closed + timedelta(seconds=observed_offset_seconds),
            ingested_at_utc=closed + timedelta(seconds=observed_offset_seconds + 1),
        ),
    )


def make_window(count: int = 4, *, timeframe: str = "H4", start: str = "2026-01-05T00:00:00Z"):
    """A window of ``count`` sequential frames on one timeframe."""
    step = Timeframe.parse(timeframe).duration
    opened = utc(start)
    frames = []
    for index in range(count):
        moment = opened + step * index
        price = Decimal("2400.00") + Decimal(index)
        frames.append(
            make_frame(
                timeframe=timeframe,
                timestamp_utc=moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                open_=str(price),
                high=str(price + Decimal("2.00")),
                low=str(price - Decimal("2.00")),
                close=str(price + Decimal("1.00")),
                indicators={"ema_50": str(price), "ema_200": "2400.00", "rsi_14": "55"},
            )
        )
    return MarketFactWindow(frames)


@pytest.fixture
def window() -> MarketFactWindow:
    return make_window()
