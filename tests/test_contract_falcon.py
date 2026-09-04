"""The FALCON output boundary: golden payloads and schema-version negotiation.

The golden files in ``fixtures/falcon/`` are the artefacts a FALCON integrator
codes against. They are only worth that if they cannot drift from what HELIOS
actually publishes, so every one of them is rebuilt here from the contract
layer and compared byte for byte.
"""

from __future__ import annotations

import json

import pytest

from helios.contracts import StrategyStateEnvelope
from helios.contracts.output import ENVELOPE_SCHEMA_VERSION, EnvelopeKind
from helios.contracts.state import StrategyState
from helios.errors import ContractViolationError, SerialisationError
from helios.integration.exemplars import (
    GOLDEN_DIR,
    GOLDEN_SCHEMA_VERSION,
    golden_exemplars,
    read_golden_document,
)
from helios.publish import (
    ENVELOPE_SCHEMA_NAMESPACE,
    SUPPORTED_ENVELOPE_SCHEMA_VERSIONS,
    InMemorySink,
    StatePublisher,
    accept_payload,
    negotiate_schema_version,
    read_schema_version,
    schema_namespace,
)

#: What each golden file is here to prove. The set is asserted against the
#: directory, so a new exemplar cannot be added without saying what it covers.
GOLDEN_CASES = {
    "atomic_matched": "an atomic state that matched",
    "atomic_no_match": "a non-match, published with an explanation",
    "chain_matched": "a chain state carrying component provenance",
    "chain_expired": "a resolved occurrence that aged out",
}


@pytest.fixture(scope="module")
def golden_dir(request):
    return request.config.rootpath / GOLDEN_DIR


@pytest.fixture(scope="module")
def exemplars():
    return {item.golden_id: item for item in golden_exemplars()}


# ------------------------------------------------------------- golden fixtures


def test_every_golden_case_is_present_and_accounted_for(golden_dir, exemplars):
    on_disk = {path.stem for path in golden_dir.glob("*.json")}
    assert on_disk == set(GOLDEN_CASES)
    assert set(exemplars) == set(GOLDEN_CASES)


@pytest.mark.parametrize("golden_id", sorted(GOLDEN_CASES))
def test_the_checked_in_golden_file_matches_what_helios_publishes(
    golden_dir, exemplars, golden_id
):
    """A drifted golden file would document a payload HELIOS no longer emits."""
    checked_in = (golden_dir / f"{golden_id}.json").read_text(encoding="utf-8")
    assert checked_in == exemplars[golden_id].render(), (
        f"fixtures/falcon/{golden_id}.json is stale; regenerate with: "
        "python3 -m helios.integration.exemplars"
    )


@pytest.mark.parametrize("golden_id", sorted(GOLDEN_CASES))
def test_a_golden_payload_is_byte_stable(golden_dir, golden_id):
    """Identical state must serialise to identical bytes, every time."""
    document = read_golden_document(golden_dir / f"{golden_id}.json")
    canonical = document["canonical_json"]
    envelope = StrategyStateEnvelope.from_canonical_json(canonical)
    assert envelope.to_canonical_json() == canonical
    # And again from a second, independently parsed instance.
    assert StrategyStateEnvelope.from_canonical_json(
        envelope.to_canonical_json()
    ).to_canonical_json() == canonical


@pytest.mark.parametrize("golden_id", sorted(GOLDEN_CASES))
def test_the_readable_payload_is_the_same_document_as_the_canonical_line(
    golden_dir, golden_id
):
    """The indented copy exists for humans; it must not say anything different."""
    document = read_golden_document(golden_dir / f"{golden_id}.json")
    assert document["payload"] == json.loads(document["canonical_json"])


@pytest.mark.parametrize("golden_id", sorted(GOLDEN_CASES))
def test_a_golden_file_documents_what_it_proves(golden_dir, golden_id):
    document = read_golden_document(golden_dir / f"{golden_id}.json")
    assert document["description"].strip()
    assert document["expectations"], "a golden fixture must state what it proves"
    assert document["golden_schema_version"] == GOLDEN_SCHEMA_VERSION
    assert document["envelope_schema_version"] == ENVELOPE_SCHEMA_VERSION


def test_the_golden_set_covers_both_kinds_and_a_resolved_state(golden_dir):
    payloads = [
        read_golden_document(path)["payload"] for path in sorted(golden_dir.glob("*.json"))
    ]
    kinds = {payload["kind"] for payload in payloads}
    states = {payload["state"] for payload in payloads}
    assert kinds == {"ATOMIC", "CHAIN"}
    assert StrategyState.MATCHED.value in states
    assert StrategyState.DORMANT.value in states
    assert StrategyState.EXPIRED.value in states


def test_a_chain_golden_carries_component_provenance(golden_dir):
    payload = read_golden_document(golden_dir / "chain_matched.json")["payload"]
    assert payload["kind"] == EnvelopeKind.CHAIN.value
    assert payload["chain_id"] == payload["strategy_id"]
    assert payload["chain_version"] == payload["strategy_version"]
    assert len(payload["components"]) == 2
    for component in payload["components"]:
        assert component["strategy_id"] and component["strategy_version"]
        assert component["state"] and component["direction"]
        assert component["contribution"]
    assert payload["explanation"]


def test_the_non_match_golden_explains_itself(golden_dir):
    payload = read_golden_document(golden_dir / "atomic_no_match.json")["payload"]
    assert payload["state"] == StrategyState.DORMANT.value
    assert payload["explanation"]
    assert payload["first_matched_at_utc"] is None
    assert payload["last_matched_at_utc"] is None
    assert payload["active_since_utc"] is None
    # Evaluated and false is not the same as never evaluated.
    assert payload["last_evaluated_at_utc"]
    assert payload["inputs"]


def test_the_expired_golden_reports_why_and_until_when(golden_dir):
    payload = read_golden_document(golden_dir / "chain_expired.json")["payload"]
    assert payload["state"] == StrategyState.EXPIRED.value
    assert payload["validity"]["valid_until_utc"]
    assert payload["validity"]["reason"]
    # The history of the occurrence that just ended is retained.
    assert payload["first_matched_at_utc"]
    assert payload["last_matched_at_utc"]
    assert payload["active_since_utc"] is None


def test_no_golden_payload_publishes_a_binary_float(golden_dir):
    """Decimals cross the boundary as exact strings, never as JSON numbers."""

    def walk(node, path="$"):
        if isinstance(node, float):
            raise AssertionError(f"{path} is a binary float")
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    for path in sorted(golden_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))["payload"]
        walk(raw, path.name)


def test_every_published_instant_is_utc(golden_dir):
    def instants(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_utc") and value is not None:
                    yield key, value
                else:
                    yield from instants(value)
        elif isinstance(node, list):
            for value in node:
                yield from instants(value)

    for path in sorted(golden_dir.glob("*.json")):
        payload = read_golden_document(path)["payload"]
        found = list(instants(payload))
        assert found, f"{path.name} publishes no instants"
        for key, value in found:
            assert value.endswith("Z"), f"{path.name}: {key} is not UTC"


def test_a_malformed_golden_document_is_refused(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ContractViolationError):
        read_golden_document(path)
    path.write_text(json.dumps({"golden_id": "x"}), encoding="utf-8")
    with pytest.raises(ContractViolationError) as caught:
        read_golden_document(path)
    assert "missing required keys" in str(caught.value)


def test_a_golden_document_from_another_format_version_is_refused(tmp_path, golden_dir):
    document = read_golden_document(golden_dir / "atomic_matched.json")
    document["golden_schema_version"] = "helios.contract_golden/9.9.9"
    path = tmp_path / "future.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractViolationError) as caught:
        read_golden_document(path)
    assert "unknown golden_schema_version" in str(caught.value)


# ------------------------------------------------------ publication round trip


@pytest.mark.parametrize("golden_id", sorted(GOLDEN_CASES))
def test_publishing_an_exemplar_emits_exactly_the_golden_bytes(exemplars, golden_id):
    """The golden files are what the publication path really produces."""
    sink = InMemorySink()
    with StatePublisher(sink) as publisher:
        payload = publisher.publish(exemplars[golden_id].envelope)
    assert payload == exemplars[golden_id].canonical_json
    assert sink.records == (payload,)


def test_a_consumer_reads_back_exactly_what_was_published(exemplars):
    sink = InMemorySink()
    publisher = StatePublisher(sink)
    try:
        publisher.publish_all(item.envelope for item in golden_exemplars())
    finally:
        publisher.close()
    for record, item in zip(sink.records, golden_exemplars()):
        assert accept_payload(record) == item.envelope


# ------------------------------------------------- schema-version negotiation


def test_helios_publishes_exactly_one_known_schema_version():
    assert SUPPORTED_ENVELOPE_SCHEMA_VERSIONS == (ENVELOPE_SCHEMA_VERSION,)
    assert schema_namespace(ENVELOPE_SCHEMA_VERSION) == ENVELOPE_SCHEMA_NAMESPACE


def test_a_consumer_accepts_a_version_it_knows():
    assert negotiate_schema_version(ENVELOPE_SCHEMA_VERSION) == ENVELOPE_SCHEMA_VERSION


@pytest.mark.parametrize(
    "declared",
    [
        "helios.strategy_state/2.0.0",
        "helios.strategy_state/1.1.0",
        "helios.strategy_state/0.9.0",
        "hermes.market_fact/1.0.0",
        "something_else",
    ],
)
def test_a_consumer_can_refuse_an_unrecognised_version(declared):
    """The whole point of publishing schema_version."""
    with pytest.raises(ContractViolationError) as caught:
        negotiate_schema_version(declared)
    message = str(caught.value)
    assert "unrecognised strategy-state schema_version" in message
    assert declared in message


def test_the_refusal_says_whether_it_was_even_a_helios_payload():
    """'HELIOS moved on without me' is a different problem from 'wrong feed'."""
    with pytest.raises(ContractViolationError) as caught:
        negotiate_schema_version("helios.strategy_state/2.0.0")
    assert caught.value.context["same_namespace"] is True
    with pytest.raises(ContractViolationError) as caught:
        negotiate_schema_version("some.other.system/1.0.0")
    assert caught.value.context["same_namespace"] is False


def test_a_consumer_may_widen_its_accepted_set_deliberately():
    accepted = (ENVELOPE_SCHEMA_VERSION, "helios.strategy_state/1.1.0")
    assert (
        negotiate_schema_version("helios.strategy_state/1.1.0", accepted)
        == "helios.strategy_state/1.1.0"
    )


def test_an_empty_accepted_set_is_refused():
    with pytest.raises(ContractViolationError) as caught:
        negotiate_schema_version(ENVELOPE_SCHEMA_VERSION, ())
    assert "at least one accepted schema_version" in str(caught.value)


def test_the_version_is_readable_without_interpreting_the_payload(exemplars):
    payload = exemplars["atomic_matched"].canonical_json
    assert read_schema_version(payload) == ENVELOPE_SCHEMA_VERSION
    # A document HELIOS could never validate still yields its declared version.
    unfamiliar = json.dumps(
        {"schema_version": "helios.strategy_state/2.0.0", "totally": "different"}
    )
    assert read_schema_version(unfamiliar) == "helios.strategy_state/2.0.0"


def test_a_payload_without_a_declared_version_cannot_be_negotiated():
    with pytest.raises(ContractViolationError) as caught:
        read_schema_version(json.dumps({"state": "MATCHED"}))
    assert "does not declare a schema_version" in str(caught.value)


def test_refusal_happens_before_interpretation(exemplars):
    """An unfamiliar payload is refused on its version, not on field errors."""
    document = json.loads(exemplars["atomic_matched"].canonical_json)
    document["schema_version"] = "helios.strategy_state/2.0.0"
    document["some_future_field"] = "value HELIOS has never heard of"
    with pytest.raises(ContractViolationError) as caught:
        accept_payload(json.dumps(document))
    assert "unrecognised strategy-state schema_version" in str(caught.value)
    assert "some_future_field" not in str(caught.value)


def test_helios_itself_refuses_to_read_a_foreign_version(exemplars):
    document = json.loads(exemplars["atomic_matched"].canonical_json)
    document["schema_version"] = "helios.strategy_state/2.0.0"
    with pytest.raises(ContractViolationError):
        StrategyStateEnvelope.from_canonical_json(json.dumps(document))


def test_a_structurally_broken_payload_is_refused():
    with pytest.raises(SerialisationError):
        accept_payload("{not json")
    with pytest.raises(ContractViolationError):
        accept_payload(json.dumps([1, 2, 3]))
