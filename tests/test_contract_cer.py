"""The CER boundary: identity compatibility, and the absence of an evidence store.

HELIOS originates ``strategy_id`` and ``strategy_version`` and carries them
cleanly on everything it publishes. CER owns ``experiment_id``, ``run_id``,
``evidence_id`` and ``artifact_id``, and owns durable empirical evidence
outright. Both halves of that are asserted here.
"""

from __future__ import annotations

import json

import pytest

from helios.contracts import (
    CER_IDENTITY_FIELDS,
    CER_OWNED_IDENTITY_FIELDS,
    StrategyId,
    StrategyIdentity,
    StrategyVersion,
)
from helios.contracts.identity import HELIOS_ORIGINATED_IDENTITY_FIELDS
from helios.contracts.output import ENVELOPE_FIELDS
from helios.errors import ContractViolationError, IdentityError
from helios.integration.cer_boundary import (
    FINGERPRINT_EXCLUDED,
    assert_promotion_immutable,
    assert_publishes_no_cer_owned_field,
    canonical_identity,
    definition_fingerprint,
    published_identity,
    strategy_identity_of,
)
from helios.integration.exemplars import golden_exemplars
from helios.spec import load_strategy_package, parse_strategy_package


@pytest.fixture(scope="module")
def envelopes():
    return {item.golden_id: item.envelope for item in golden_exemplars()}


@pytest.fixture(scope="module")
def handoff_dir(request):
    return request.config.rootpath / "fixtures" / "hsa" / "handoff"


@pytest.fixture(scope="module")
def package(handoff_dir):
    return load_strategy_package(handoff_dir / "golden_cross.atomic.yaml")


# ------------------------------------------------- the identity HELIOS carries


def test_helios_originates_exactly_two_of_cers_canonical_identifiers():
    assert HELIOS_ORIGINATED_IDENTITY_FIELDS == ("strategy_id", "strategy_version")
    assert CER_OWNED_IDENTITY_FIELDS == (
        "experiment_id",
        "run_id",
        "evidence_id",
        "artifact_id",
    )
    assert set(CER_IDENTITY_FIELDS) == set(HELIOS_ORIGINATED_IDENTITY_FIELDS) | set(
        CER_OWNED_IDENTITY_FIELDS
    )


@pytest.mark.parametrize(
    "golden_id", ["atomic_matched", "atomic_no_match", "chain_matched", "chain_expired"]
)
def test_every_published_state_carries_cer_identity(envelopes, golden_id):
    envelope = envelopes[golden_id]
    fields = published_identity(envelope)
    assert set(fields) == set(HELIOS_ORIGINATED_IDENTITY_FIELDS)
    assert all(value for value in fields.values())


def test_identity_reads_the_same_way_for_an_atomic_and_a_chain(envelopes):
    """CER never branches on kind to learn who produced a state."""
    for golden_id in ("atomic_matched", "chain_matched"):
        envelope = envelopes[golden_id]
        assert published_identity(envelope)["strategy_id"] == str(envelope.strategy_id)
    assert canonical_identity(envelopes["atomic_matched"]) == "golden_cross@1.0.0"
    assert (
        canonical_identity(envelopes["chain_matched"]) == "gold_context_trigger@1.0.0"
    )


def test_published_identity_round_trips_through_the_canonical_form(envelopes):
    for envelope in envelopes.values():
        identity = strategy_identity_of(envelope)
        assert StrategyIdentity.parse(identity.canonical) == identity


def test_published_identifiers_satisfy_the_canonical_identity_model(envelopes):
    """Unambiguous by construction: a strict id pattern and a strict version."""
    for envelope in envelopes.values():
        fields = published_identity(envelope)
        assert StrategyId(fields["strategy_id"]).value == fields["strategy_id"]
        version = StrategyVersion.parse(fields["strategy_version"])
        assert str(version) == fields["strategy_version"]
        assert (version.major, version.minor, version.patch) >= (0, 0, 0)


@pytest.mark.parametrize(
    "malformed", ["01.0.0", "1.0", "1.0.0-rc1", "v1.0.0", "1.0.0.0", ""]
)
def test_an_ambiguous_version_could_never_be_published(malformed):
    """CER records evidence against this string; it must have one reading."""
    with pytest.raises(IdentityError):
        StrategyVersion.parse(malformed)


def test_a_published_payload_is_checked_for_cer_owned_fields(envelopes):
    for envelope in envelopes.values():
        payload = json.loads(envelope.to_canonical_json())
        assert_publishes_no_cer_owned_field(payload)


def test_a_payload_minting_evidence_identity_is_refused(envelopes):
    payload = json.loads(envelopes["atomic_matched"].to_canonical_json())
    payload["run_id"] = "r-0001"
    payload["evidence_id"] = "e-0001"
    with pytest.raises(IdentityError) as caught:
        assert_publishes_no_cer_owned_field(payload)
    assert caught.value.context["offending_fields"] == ["evidence_id", "run_id"]


def test_helios_publishes_no_cer_owned_field_anywhere_in_the_envelope():
    for name in CER_OWNED_IDENTITY_FIELDS:
        assert name not in ENVELOPE_FIELDS


def test_published_identity_requires_a_real_envelope():
    with pytest.raises(ContractViolationError):
        published_identity({"strategy_id": "golden_cross"})  # type: ignore[arg-type]


# ------------------------------------------------------ promotion immutability


def test_a_definition_fingerprint_is_deterministic(package, handoff_dir):
    again = load_strategy_package(handoff_dir / "golden_cross.atomic.yaml")
    assert definition_fingerprint(package) == definition_fingerprint(again)
    assert len(definition_fingerprint(package)) == 64


def test_metadata_is_not_part_of_the_definition(package):
    """Correcting a description is not a logic change and must not force a bump."""
    assert FINGERPRINT_EXCLUDED == ("metadata", "identity.strategy_version")
    document = json.loads(package.model_dump_json())
    document["metadata"]["description"] = "A clearer wording of the same rule."
    document["metadata"]["title"] = "Golden / death cross (renamed)"
    reworded = parse_strategy_package(document, origin="test")
    assert definition_fingerprint(reworded) == definition_fingerprint(package)


def test_a_changed_parameter_changes_the_definition(package):
    document = json.loads(package.model_dump_json())
    document["parameters"]["min_separation"]["value"] = "0.50"
    changed = parse_strategy_package(document, origin="test")
    assert definition_fingerprint(changed) != definition_fingerprint(package)


def test_a_changed_input_binding_changes_the_definition(package):
    document = json.loads(package.model_dump_json())
    document["inputs"][0]["timeframe"] = "D1"
    changed = parse_strategy_package(document, origin="test")
    assert definition_fingerprint(changed) != definition_fingerprint(package)


def test_a_changed_chain_component_changes_the_definition(handoff_dir):
    chain = load_strategy_package(handoff_dir / "gold_context_trigger.chain.yaml")
    document = json.loads(chain.model_dump_json())
    document["chain"]["components"][1]["required_states"] = ["MATCHED", "ACTIVE"]
    changed = parse_strategy_package(document, origin="test")
    assert definition_fingerprint(changed) != definition_fingerprint(chain)


def test_the_version_itself_is_excluded_from_the_fingerprint(package):
    """Otherwise the fingerprint could never be compared across versions."""
    document = json.loads(package.model_dump_json())
    document["identity"]["strategy_version"] = "2.0.0"
    bumped = parse_strategy_package(document, origin="test")
    assert definition_fingerprint(bumped) == definition_fingerprint(package)


def test_a_promoted_version_cannot_be_silently_mutated(package):
    """A logic change means a NEW version, never an in-place tweak."""
    document = json.loads(package.model_dump_json())
    document["parameters"]["min_separation"]["value"] = "0.50"
    mutated = parse_strategy_package(document, origin="test")
    with pytest.raises(IdentityError) as caught:
        assert_promotion_immutable(package, mutated)
    message = str(caught.value)
    assert "promoted strategy_version is immutable" in message
    assert "golden_cross@1.0.0" in message


def test_a_logic_change_under_a_new_version_is_accepted(package):
    document = json.loads(package.model_dump_json())
    document["parameters"]["min_separation"]["value"] = "0.50"
    document["identity"]["strategy_version"] = "2.0.0"
    promoted_again = parse_strategy_package(document, origin="test")
    assert_promotion_immutable(package, promoted_again)


def test_an_unchanged_definition_may_not_move_its_version(package):
    """A version that moves for no reason makes CER's record meaningless too."""
    document = json.loads(package.model_dump_json())
    document["identity"]["strategy_version"] = "1.0.1"
    bumped = parse_strategy_package(document, origin="test")
    with pytest.raises(IdentityError) as caught:
        assert_promotion_immutable(package, bumped)
    assert "without a definition change" in str(caught.value)


def test_republishing_the_same_definition_is_fine(package, handoff_dir):
    again = load_strategy_package(handoff_dir / "golden_cross.atomic.yaml")
    assert_promotion_immutable(package, again)


def test_a_different_strategy_cannot_reuse_a_promoted_identity(package):
    document = json.loads(package.model_dump_json())
    document["identity"]["strategy_id"] = "range_breakout"
    other = parse_strategy_package(document, origin="test")
    with pytest.raises(IdentityError) as caught:
        assert_promotion_immutable(package, other)
    assert "strategy_id mismatch" in str(caught.value)


def test_a_fingerprint_requires_a_real_package():
    with pytest.raises(ContractViolationError):
        definition_fingerprint({"identity": {}})  # type: ignore[arg-type]


# -------------------------------------------- HELIOS is not an evidence store


def test_the_cer_boundary_stores_nothing():
    """Compatibility and documentation only: no registry, no tables, no state."""
    import helios.integration.cer_boundary as boundary

    for name in dir(boundary):
        if name.startswith("_"):
            continue
        value = getattr(boundary, name)
        assert not isinstance(value, (list, dict, set)), (
            f"{name} is mutable module-level state; the CER boundary must hold none"
        )


def test_the_cer_boundary_opens_no_storage():
    import ast
    from pathlib import Path

    forbidden = {
        "sqlite3", "psycopg2", "pymysql", "mysql", "sqlalchemy", "redis",
        "shelve", "dbm", "pickle", "csv",
    }
    path = Path(__file__).resolve().parents[1] / "helios" / "integration"
    for module in sorted(path.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, module
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, module
