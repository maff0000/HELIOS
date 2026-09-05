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
        "swing_proximity@1.0.0",
        "gold_context_trigger@1.0.0",
        "gold_staged_sequence@1.0.0",
    }


def test_the_two_swing_proximity_files_describe_one_definition(valid_dir):
    """A promoted (strategy_id, strategy_version) is immutable.

    ``swing_proximity@1.0.0`` is checked in twice on purpose: once as the
    LOCATION component the valid fixture set needs to resolve on its own, and
    once as the reference package shipped beside the code. Two files claiming
    one promoted version must describe one strategy, or published state would
    not be reproducible — so they are held to that here rather than left to
    drift.
    """
    from helios.strategies.catalogue import REFERENCE_PACKAGE_DIRECTORY

    assert load_strategy_package(
        valid_dir / "swing_proximity.atomic.yaml"
    ) == load_strategy_package(
        REFERENCE_PACKAGE_DIRECTORY / "swing_proximity.atomic.yaml"
    )


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
    """HSA must be able to see which HERMES facts it may require.

    Asserted against the PUBLISHED document. This previously asserted on
    ``FACT_FIELDS``, a Python constant — which is true whatever the schema
    says, so it never tested the property its own docstring claims.
    """
    schema = json.loads((repo_root / SCHEMA_PATH).read_text(encoding="utf-8"))
    assert "InputRequirement" in schema["$defs"]
    published = schema["$defs"]["InputRequirement"]["properties"]["required_fields"]
    assert published["items"]["enum"] == sorted(FACT_FIELDS)
    assert set(published["items"]["enum"]) >= {
        "close", "ema_50", "ema_200", "atr_14", "rsi_14"
    }


# ------------------------------------ the published schema, under a real validator
#
# The schema is the artefact an HSA author targets. Asserting things about the
# Python model tells us nothing about what that author is checked against, so
# everything below runs the CHECKED-IN document through a real Draft 2020-12
# validator against the same fixtures the Python loader is held to.


def _as_json_document(path):
    """The fixture as an HSA author would submit it: plain JSON types.

    The YAML fixtures use unquoted timestamps, which YAML types as datetime and
    JSON has no notion of. Normalising here rather than loading through HELIOS
    is deliberate: this test must not depend on the code it is checking.
    """
    import datetime as _datetime
    import decimal as _decimal

    import yaml as _yaml

    def plain(value):
        if isinstance(value, dict):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, list):
            return [plain(item) for item in value]
        if isinstance(value, _datetime.datetime):
            return value.astimezone(_datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        if isinstance(value, _decimal.Decimal):
            return float(value)
        return value

    text = path.read_text(encoding="utf-8")
    document = json.loads(text) if path.suffix == ".json" else _yaml.safe_load(text)
    return plain(document)


@pytest.fixture(scope="module")
def published_validator(repo_root):
    from jsonschema import Draft202012Validator

    schema = json.loads((repo_root / SCHEMA_PATH).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


#: Every legitimate package checked into this repository. A schema that rejects
#: work HSA is entitled to submit is worse than one that is too permissive.
LEGITIMATE_PACKAGE_DIRS = (
    "fixtures/strategy_packages/valid",
    "fixtures/strategy_packages/scenario",
    "fixtures/hsa/handoff",
    "helios/strategies/packages",
)


def _packages_in(repo_root, directory):
    return sorted(
        path for path in (repo_root / directory).rglob("*") if path.is_file()
    )


def test_every_legitimate_package_validates_against_the_published_schema(
    repo_root, published_validator
):
    for directory in LEGITIMATE_PACKAGE_DIRS:
        paths = _packages_in(repo_root, directory)
        assert paths, directory
        for path in paths:
            errors = list(published_validator.iter_errors(_as_json_document(path)))
            assert not errors, f"{path}: {[error.message for error in errors]}"


#: Which malformed fixtures the PUBLISHED schema rejects on its own, and which
#: it cannot. ``False`` is not a gap left open by accident — it names a rule
#: with no Draft 2020-12 expression, and ``helios.spec.schema.PYTHON_ONLY_RULES``
#: is the schema's own statement of the same two.
SCHEMA_CATCHES = {
    "all_chain_with_ordering.yaml": True,
    "atomic_with_chain.yaml": True,
    "context_trigger_without_roles.yaml": True,
    "duplicate_role.yaml": False,          # uniqueness by key: inexpressible
    "expiry_frames_without_count.yaml": True,
    "parameter_out_of_range.yaml": False,  # sibling comparison: inexpressible
    "sequence_without_window.yaml": True,
    "unknown_key_typo.yaml": True,
    "unknown_required_field.yaml": True,
    "unknown_schema_version.yaml": True,
}

UNDERSPECIFIED_CATCHES = {
    "requires_unpublished_fact.atomic.yaml": True,
    "sequence_without_ordering_window.chain.yaml": True,
}


@pytest.mark.parametrize(("name", "rejected"), sorted(SCHEMA_CATCHES.items()))
def test_the_published_schema_rejects_the_malformed_packages(
    malformed_dir, published_validator, name, rejected
):
    """Measured, not assumed: this was 2 of 10 before the schema was tightened."""
    errors = list(published_validator.iter_errors(_as_json_document(malformed_dir / name)))
    assert bool(errors) is rejected, (
        f"{name}: schema {'accepted' if not errors else 'rejected'} it, expected "
        f"{'rejected' if rejected else 'accepted'}"
    )


@pytest.mark.parametrize(("name", "rejected"), sorted(UNDERSPECIFIED_CATCHES.items()))
def test_the_published_schema_rejects_the_under_specified_packages(
    repo_root, published_validator, name, rejected
):
    path = repo_root / "fixtures" / "hsa" / "underspecified" / name
    errors = list(published_validator.iter_errors(_as_json_document(path)))
    assert bool(errors) is rejected, name


def test_every_malformed_fixture_is_measured_against_the_published_schema(
    malformed_dir,
):
    """No fixture may be quietly dropped from the measurement above."""
    on_disk = {path.name for path in malformed_dir.iterdir() if path.is_file()}
    assert on_disk == set(SCHEMA_CATCHES)


def test_the_schema_states_the_rules_it_cannot_express(repo_root):
    """The residual gap is published, not silent.

    An author who passes this schema and is then refused by HELIOS must be able
    to see, from the artefact itself, that passing it was never sufficient.
    """
    from helios.spec.schema import PYTHON_ONLY_RULES

    assert len(PYTHON_ONLY_RULES) == sum(
        1 for rejected in SCHEMA_CATCHES.values() if not rejected
    )
    description = json.loads(
        (repo_root / SCHEMA_PATH).read_text(encoding="utf-8")
    )["description"]
    assert "necessary but not sufficient" in description
    for rule in PYTHON_ONLY_RULES:
        assert rule in description


@pytest.mark.parametrize("name", sorted(SCHEMA_CATCHES) )
def test_python_refuses_every_malformed_package_the_schema_cannot(
    malformed_dir, name
):
    """Whatever the schema misses, the loader must still refuse."""
    with pytest.raises(StrategySpecError):
        load_strategy_package(malformed_dir / name)
