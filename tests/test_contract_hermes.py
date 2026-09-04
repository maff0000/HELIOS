"""The HERMES input boundary: what HELIOS accepts, and what it refuses loudly.

Everything here runs against the canonical fixtures in ``fixtures/hermes/``,
which go through exactly the same contract validation a live feed would. A
fixture HELIOS accepts is a fact the real contract accepts.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from helios.contracts import (
    CANDLE_FIELDS,
    FACT_FIELDS,
    INDICATOR_FIELDS,
    ALL_TIMEFRAMES,
    FreshnessPolicy,
    Timeframe,
)
from helios.contracts.market_fact import MARKET_FACT_SCHEMA_VERSION
from helios.errors import (
    ContractViolationError,
    HeliosError,
    MissingFactError,
    StaleFactError,
)
from helios.hermes import load_fixture, load_fixture_frames
from helios.integration.hermes_boundary import (
    INPUT_REJECTION_RULES,
    PROVENANCE_FIELDS,
    accept_market_facts,
    describe_input_contract,
    input_freshness,
)
from tests._scan import code_tokens, python_files

ACCEPTED = ("hermes.market_fact/1.0.0",)


@pytest.fixture(scope="module")
def canonical_dir(request):
    return request.config.rootpath / "fixtures" / "hermes" / "xau_usd"


@pytest.fixture(scope="module")
def malformed_dir(request):
    return request.config.rootpath / "fixtures" / "hermes" / "malformed"


@pytest.fixture(scope="module")
def stale_dir(request):
    return request.config.rootpath / "fixtures" / "hermes" / "stale"


# ------------------------------------------------------- what HELIOS requires


def test_the_input_contract_describes_itself_from_the_models():
    described = describe_input_contract()
    assert described["fact_schema_version"] == MARKET_FACT_SCHEMA_VERSION
    assert described["required_candle_fields"] == list(CANDLE_FIELDS)
    assert described["optional_indicator_fields"] == list(INDICATOR_FIELDS)
    assert described["required_provenance_fields"] == list(PROVENANCE_FIELDS)
    assert [item["helios_code"] for item in described["timeframes"]] == [
        timeframe.code for timeframe in ALL_TIMEFRAMES
    ]
    assert described["rejects"] == list(INPUT_REJECTION_RULES)


def test_the_described_fields_are_the_fields_a_package_may_require():
    described = describe_input_contract()
    declared = set(described["required_candle_fields"]) | set(
        described["optional_indicator_fields"]
    )
    assert declared == set(FACT_FIELDS)


def test_provenance_is_derived_from_the_contract_not_restated():
    assert PROVENANCE_FIELDS == (
        "source",
        "schema_version",
        "observed_at_utc",
        "ingested_at_utc",
    )


def test_the_input_boundary_carries_no_infrastructure_detail():
    """This is a data contract. A public repository must carry no topology.

    Code and data are scanned; prose is not. A docstring must be able to say
    "there is no connection string here" in order to explain the rule — that
    is the opposite of breaking it. Same doctrine as ``tests/_scan.py``.
    """
    banned = (
        "password", "secret", "api_key", "apikey", "credential", "passwd",
        "private_key", "connection_string", "host=", "port=", "://",
        "localhost", "127.0.0.1", "jdbc", "dsn",
    )
    package = Path(__file__).resolve().parents[1] / "helios" / "integration"
    for path in python_files(package):
        for kind, text in code_tokens(path):
            lowered = text.lower()
            for word in banned:
                assert word not in lowered, f"{path}: {kind} {text!r} mentions {word}"


def test_helios_ships_no_live_hermes_client():
    """Facts arrive as validated values; the transport is not HELIOS's business."""
    forbidden = {
        "socket", "http", "urllib", "requests", "httpx", "aiohttp", "sqlite3",
        "psycopg2", "pymysql", "mysql", "sqlalchemy", "redis", "kafka", "pika",
    }
    package = Path(__file__).resolve().parents[1] / "helios" / "hermes"
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, path


# ------------------------------------------------------------ accepting facts


CANONICAL = {
    "xau_usd_h4.json": Timeframe.H4,
    "xau_usd_h4_death_cross.json": Timeframe.H4,
    "xau_usd_h1.json": Timeframe.H1,
    "xau_usd_m15.json": Timeframe.M15,
    "xau_usd_m5.json": Timeframe.M5,
}


@pytest.mark.parametrize(("name", "timeframe"), sorted(CANONICAL.items()))
def test_well_formed_hermes_facts_are_accepted(canonical_dir, policy, name, timeframe):
    fixture = load_fixture(canonical_dir / name)
    window = accept_market_facts(
        fixture.frames,
        policy=policy,
        now_utc=fixture.reference_now_utc,
        accepted_schema_versions=ACCEPTED,
    )
    assert window.timeframe is timeframe
    assert len(window) == len(fixture.frames)
    assert window.frames == fixture.frames


def test_acceptance_covers_the_whole_gold_template(canonical_dir, policy):
    """4H context, 1H location, 15M confirmation, 5M trigger — all accepted."""
    accepted = set()
    for name in CANONICAL:
        fixture = load_fixture(canonical_dir / name)
        window = accept_market_facts(
            fixture.frames,
            policy=policy,
            now_utc=fixture.reference_now_utc,
            accepted_schema_versions=ACCEPTED,
        )
        accepted.add(window.timeframe)
    assert {Timeframe.H4, Timeframe.H1, Timeframe.M15, Timeframe.M5} <= accepted


def test_accepted_facts_publish_their_freshness(canonical_dir, policy):
    fixture = load_fixture(canonical_dir / "xau_usd_h4.json")
    window = accept_market_facts(
        fixture.frames, policy=policy, now_utc=fixture.reference_now_utc
    )
    record = input_freshness(
        window, policy=policy, now_utc=fixture.reference_now_utc, semantic_role="CONTEXT"
    )
    assert record.is_fresh and record.is_complete
    assert record.age_seconds == 60
    assert record.max_age_seconds == 14400 * 3 // 2 + 60
    assert str(record.semantic_role) == "CONTEXT"
    assert record.schema_version == ACCEPTED[0]


# --------------------------------------------------------- failing loudly


MALFORMED_CASES = {
    "missing_candle_field.json": "malformed Candle",
    "high_below_low.json": "high is below low",
    "unknown_candle_field.json": "malformed Candle",
    "naive_timestamp.json": "does not assume a timezone",
    "misaligned_timestamp.json": "plausible bar-open instant",
    "negative_volume.json": "volume must not be negative",
    "rsi_out_of_range.json": "rsi_14 outside 0..100",
    "ingested_before_observed.json": "ingested before observed",
    "unsupported_timeframe.json": "unsupported timeframe",
}


@pytest.mark.parametrize(("name", "expected"), sorted(MALFORMED_CASES.items()))
def test_malformed_facts_are_refused_at_the_boundary(malformed_dir, name, expected):
    """Refused while building the frames — nothing reaches an evaluation."""
    with pytest.raises(HeliosError) as caught:
        load_fixture_frames(malformed_dir / name)
    message = str(caught.value)
    assert expected in message
    assert name in message


def test_an_unknown_field_is_refused_rather_than_passed_through(malformed_dir):
    """A HERMES schema change must be a deliberate HELIOS decision."""
    with pytest.raises(ContractViolationError) as caught:
        load_fixture_frames(malformed_dir / "unknown_candle_field.json")
    problems = caught.value.context.get("problems", [])
    assert any("extra" in str(problem).lower() for problem in problems)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("out_of_order.json", "ascending time order"),
        ("duplicate_timestamp.json", "duplicate timestamp"),
        ("mixed_instrument.json", "mixes instruments"),
    ],
)
def test_out_of_order_and_incoherent_windows_are_refused(
    malformed_dir, policy, name, expected
):
    """Frames are never silently re-sorted: that would make order irrelevant."""
    frames = load_fixture_frames(malformed_dir / name)
    with pytest.raises(ContractViolationError) as caught:
        accept_market_facts(
            frames, policy=policy, now_utc=frames[-1].close_time_utc
        )
    assert expected in str(caught.value)


def test_a_stale_fact_is_refused_with_its_measurements(stale_dir, policy):
    fixture = load_fixture(stale_dir / "xau_usd_h4_stale.json")
    with pytest.raises(StaleFactError) as caught:
        accept_market_facts(
            fixture.frames,
            policy=policy,
            now_utc=fixture.reference_now_utc,
            accepted_schema_versions=ACCEPTED,
        )
    context = caught.value.context
    assert context["age_seconds"] > context["max_age_seconds"]
    assert context["timeframe"] == "H4"
    assert context["instrument"] == "XAU_USD"


def test_an_incomplete_bar_obeys_the_configured_policy(stale_dir, policy):
    """Whether a forming bar may be evaluated is configuration, not a default."""
    fixture = load_fixture(stale_dir / "xau_usd_h4_incomplete_last_bar.json")
    with pytest.raises(StaleFactError) as caught:
        accept_market_facts(
            fixture.frames, policy=policy, now_utc=fixture.reference_now_utc
        )
    assert "incomplete bar" in str(caught.value)

    permissive = FreshnessPolicy(
        max_age_multiplier=policy.max_age_multiplier,
        grace=policy.grace,
        allow_incomplete_frames=True,
    )
    window = accept_market_facts(
        fixture.frames, policy=permissive, now_utc=fixture.reference_now_utc
    )
    record = input_freshness(
        window, policy=permissive, now_utc=fixture.reference_now_utc
    )
    assert record.is_fresh and not record.is_complete


def test_a_fact_schema_version_this_deployment_does_not_accept_is_refused(
    canonical_dir, policy
):
    fixture = load_fixture(canonical_dir / "xau_usd_h4.json")
    with pytest.raises(ContractViolationError) as caught:
        accept_market_facts(
            fixture.frames,
            policy=policy,
            now_utc=fixture.reference_now_utc,
            accepted_schema_versions=("hermes.market_fact/2.0.0",),
        )
    message = str(caught.value)
    assert "not configured to accept" in message
    assert "hermes.market_fact/1.0.0" in message


def test_a_deployment_must_accept_at_least_one_schema_version(canonical_dir, policy):
    fixture = load_fixture(canonical_dir / "xau_usd_h4.json")
    with pytest.raises(ContractViolationError) as caught:
        accept_market_facts(
            fixture.frames,
            policy=policy,
            now_utc=fixture.reference_now_utc,
            accepted_schema_versions=(),
        )
    assert "at least one HERMES fact schema version" in str(caught.value)


def test_incomplete_input_is_refused_rather_than_padded(canonical_dir, policy):
    """A strategy that declared it needs 200 bars must never get 12."""
    fixture = load_fixture(canonical_dir / "xau_usd_h4.json")
    window = accept_market_facts(
        fixture.frames, policy=policy, now_utc=fixture.reference_now_utc
    )
    with pytest.raises(MissingFactError) as caught:
        window.lookback(200)
    assert caught.value.context["requested"] == 200
    assert caught.value.context["available"] == len(window)


def test_an_absent_indicator_is_never_substituted_with_zero(canonical_dir, policy):
    fixture = load_fixture(canonical_dir / "xau_usd_m15.json")
    window = accept_market_facts(
        fixture.frames, policy=policy, now_utc=fixture.reference_now_utc
    )
    frame = window.latest
    if "ema_200" in frame.indicators.available:
        pytest.skip("this fixture supplies ema_200")
    with pytest.raises(MissingFactError) as caught:
        frame.fact("ema_200")
    assert caught.value.context["field"] == "ema_200"


def test_nothing_at_all_is_refused(policy):
    from helios.clock import from_iso8601_utc

    with pytest.raises(ContractViolationError) as caught:
        accept_market_facts(
            (), policy=policy, now_utc=from_iso8601_utc("2026-01-07T00:00:00Z")
        )
    assert "at least one frame" in str(caught.value)
