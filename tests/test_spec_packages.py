"""The HSA-targetable strategy package format and its loader."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from helios.contracts import FACT_FIELDS, Timeframe
from helios.errors import StrategySpecError
from helios.spec import (
    STRATEGY_PACKAGE_SCHEMA_VERSION,
    ChainPrimitive,
    PackageKind,
    load_strategy_package,
    load_strategy_packages,
    parse_strategy_package,
    render_schema,
    strategy_package_json_schema,
)
from helios.spec.schema import SCHEMA_PATH


@pytest.fixture(scope="module")
def valid_dir(request):
    return request.config.rootpath / "fixtures" / "strategy_packages" / "valid"


@pytest.fixture(scope="module")
def malformed_dir(request):
    return request.config.rootpath / "fixtures" / "strategy_packages" / "malformed"


# ------------------------------------------------------------------- loading


def test_every_valid_package_loads(valid_dir):
    packages = load_strategy_packages(valid_dir)
    assert set(packages) == {
        "golden_cross@1.0.0",
        "range_breakout@1.0.0",
        "rejection_wick@1.0.0",
        "gold_context_trigger@1.0.0",
        "gold_staged_sequence@1.0.0",
    }


def test_yaml_and_json_are_both_accepted(valid_dir):
    assert load_strategy_package(valid_dir / "golden_cross.atomic.yaml").kind is PackageKind.ATOMIC
    assert load_strategy_package(valid_dir / "rejection_wick.atomic.json").kind is PackageKind.ATOMIC


def test_numbers_are_read_as_exact_decimals(valid_dir):
    """A YAML 0.6 must not become a binary float on the way in."""
    package = load_strategy_package(valid_dir / "rejection_wick.atomic.json")
    value = package.parameters["min_wick_ratio"].value
    assert isinstance(value, Decimal)
    assert value == Decimal("0.6")


def test_a_package_declares_its_own_role_to_timeframe_mapping(valid_dir):
    """The PID template lives in the package, never in HELIOS source."""
    chain = load_strategy_package(valid_dir / "gold_sequence.chain.yaml")
    assert chain.role_timeframes == {
        "CONTEXT": Timeframe.H4,
        "LOCATION": Timeframe.H1,
        "CONFIRMATION": Timeframe.M15,
        "TRIGGER": Timeframe.M5,
    }


def test_two_packages_may_map_the_same_role_differently():
    """Proof there is no global role mapping."""
    base = {
        "schema_version": STRATEGY_PACKAGE_SCHEMA_VERSION,
        "kind": "ATOMIC",
        "identity": {"strategy_id": "role_probe", "strategy_version": "1.0.0"},
        "metadata": {
            "title": "Role probe",
            "description": "Binds CONTEXT to an unusual timeframe.",
            "authored_by": "HSA",
            "authored_at_utc": "2026-01-02T09:00:00Z",
        },
        "parameters": {},
        "direction": {"mode": "DIRECTIONAL", "resolution": "STRATEGY_LOCAL"},
        "timing": {"evaluate_on": "CLOSED_FRAME"},
        "persistence": {"min_matched_frames": 1, "weakening_enabled": False},
        "expiry": {"mode": "NEVER"},
    }
    on_d1 = parse_strategy_package(
        {**base, "inputs": [{"role": "CONTEXT", "timeframe": "D1", "lookback": 2,
                             "required_fields": ["close"]}]},
        origin="test",
    )
    on_m15 = parse_strategy_package(
        {**base, "inputs": [{"role": "CONTEXT", "timeframe": "M15", "lookback": 2,
                             "required_fields": ["close"]}]},
        origin="test",
    )
    assert on_d1.role_timeframes["CONTEXT"] is Timeframe.D1
    assert on_m15.role_timeframes["CONTEXT"] is Timeframe.M15


def test_chain_primitives_are_the_canonical_four():
    assert {member.value for member in ChainPrimitive} == {
        "ALL", "ANY", "SEQUENCE", "CONTEXT_TRIGGER"
    }


def test_a_sequence_chain_declares_a_complete_ordering(valid_dir):
    chain = load_strategy_package(valid_dir / "gold_sequence.chain.yaml").chain
    assert chain.primitive is ChainPrimitive.SEQUENCE
    assert [component.sequence_index for component in chain.components] == [0, 1, 2, 3]
    assert chain.ordering_window_seconds == 28800


def test_a_context_trigger_chain_names_its_context_and_trigger(valid_dir):
    chain = load_strategy_package(valid_dir / "gold_context_trigger.chain.yaml").chain
    roles = sorted(str(component.role) for component in chain.components)
    assert roles == ["CONTEXT", "TRIGGER"]


def test_packages_are_immutable(valid_dir):
    package = load_strategy_package(valid_dir / "golden_cross.atomic.yaml")
    with pytest.raises(Exception):
        package.kind = PackageKind.CHAIN  # type: ignore[misc]
    with pytest.raises(Exception):
        package.parameters["injected"] = None  # type: ignore[index]


# --------------------------------------------------- loud failure, every case


MALFORMED_CASES = {
    "unknown_schema_version.yaml": "schema_version",
    "unknown_required_field.yaml": "HELIOS does not know",
    "expiry_frames_without_count.yaml": "requires 'frames'",
    "unknown_key_typo.yaml": "malformed strategy package",
    "duplicate_role.yaml": "more than one input",
    "parameter_out_of_range.yaml": "above its declared maximum",
    "atomic_with_chain.yaml": "must not declare a chain",
    "sequence_without_window.yaml": "ordering_window_seconds",
    "all_chain_with_ordering.yaml": "only a SEQUENCE chain",
    "context_trigger_without_roles.yaml": "must declare its role",
}


@pytest.mark.parametrize(("name", "expected"), sorted(MALFORMED_CASES.items()))
def test_malformed_packages_fail_loudly_and_precisely(malformed_dir, name, expected):
    with pytest.raises(StrategySpecError) as caught:
        load_strategy_package(malformed_dir / name)
    message = str(caught.value)
    assert expected in message
    assert name in message  # the failure names the offending file


def test_every_malformed_fixture_is_covered(malformed_dir):
    on_disk = {path.name for path in malformed_dir.iterdir() if path.is_file()}
    assert on_disk == set(MALFORMED_CASES)


def test_helios_never_invents_a_missing_definition(malformed_dir):
    """The whole point: an ambiguous package is returned to HSA, not guessed at."""
    with pytest.raises(StrategySpecError):
        load_strategy_package(malformed_dir / "sequence_without_window.yaml")


@pytest.mark.parametrize(
    "data",
    [
        [],
        "a string",
        None,
        {"schema_version": STRATEGY_PACKAGE_SCHEMA_VERSION},
    ],
)
def test_structurally_wrong_input_fails_loudly(data):
    with pytest.raises(StrategySpecError):
        parse_strategy_package(data, origin="test")


def test_an_unsupported_file_format_fails_loudly(tmp_path):
    path = tmp_path / "package.txt"
    path.write_text("kind: ATOMIC\n", encoding="utf-8")
    with pytest.raises(StrategySpecError) as caught:
        load_strategy_package(path)
    assert "unsupported strategy package format" in str(caught.value)


def test_a_missing_package_fails_loudly(tmp_path):
    with pytest.raises(StrategySpecError):
        load_strategy_package(tmp_path / "absent.yaml")


def test_malformed_yaml_fails_loudly(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("kind: [unclosed\n", encoding="utf-8")
    with pytest.raises(StrategySpecError) as caught:
        load_strategy_package(path)
    assert "malformed YAML" in str(caught.value)


def test_duplicate_identities_across_packages_fail_loudly(tmp_path, valid_dir):
    source = (valid_dir / "golden_cross.atomic.yaml").read_text(encoding="utf-8")
    (tmp_path / "a.yaml").write_text(source, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(source, encoding="utf-8")
    with pytest.raises(StrategySpecError) as caught:
        load_strategy_packages(tmp_path)
    assert "duplicate strategy identity" in str(caught.value)


def test_chain_of_chains_is_not_expressible():
    """v1 chains consume atomic strategies directly, per the PID."""
    from helios.spec.model import ChainComponent

    assert "chain" not in ChainComponent.model_fields
    assert set(ChainComponent.model_fields) == {
        "strategy_id", "strategy_version", "role", "sequence_index",
        "direction_relationship", "required_states",
    }


# ------------------------------------------------------------- schema for HSA


def test_the_json_schema_describes_the_package(valid_dir):
    schema = strategy_package_json_schema()
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert set(schema["required"]) >= {
        "schema_version", "kind", "identity", "metadata",
        "inputs", "parameters", "direction", "timing", "persistence", "expiry",
    }


def test_the_checked_in_schema_matches_the_model(repo_root):
    """A drifted schema would silently misdescribe what HELIOS accepts."""
    checked_in = (repo_root / SCHEMA_PATH).read_text(encoding="utf-8")
    assert checked_in == render_schema(), (
        "docs/schema/strategy_package.schema.json is stale; "
        "regenerate with: python3 -m helios.spec.schema"
    )


def test_the_schema_enumerates_the_known_fact_fields(repo_root):
    """HSA must be able to see which HERMES facts it may require."""
    schema = json.loads((repo_root / SCHEMA_PATH).read_text(encoding="utf-8"))
    assert "InputRequirement" in schema["$defs"]
    assert set(FACT_FIELDS) >= {"close", "ema_50", "ema_200", "atr_14", "rsi_14"}
