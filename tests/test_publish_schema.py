"""The published JSON Schema FALCON validates against.

Two things must hold, and both are checked mechanically:

* the checked-in document must equal what the model generates today, so it can
  never quietly stop describing HELIOS;
* every golden payload HELIOS publishes must conform to it, so the document is
  not merely self-consistent but actually true of the wire format.

The conformance check is a small validator for the JSON Schema subset this
document uses (``type``, ``enum``, ``pattern``, ``anyOf``, ``$ref``,
``properties``, ``required``, ``additionalProperties``, ``items``). It is
deliberately not a general-purpose implementation, and
``test_the_conformance_check_would_catch_a_violation`` proves it can fail.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from helios.contracts.output import ENVELOPE_FIELDS, ENVELOPE_SCHEMA_VERSION
from helios.integration.exemplars import golden_exemplars
from helios.publish.schema import (
    CANONICAL_DECIMAL_PATTERN,
    CANONICAL_INSTANT_PATTERN,
    SCHEMA_PATH,
    published_schema,
    render_schema,
)


@pytest.fixture(scope="module")
def schema():
    return published_schema()


@pytest.fixture(scope="module")
def payloads():
    return {
        item.golden_id: json.loads(item.canonical_json) for item in golden_exemplars()
    }


# ------------------------------------------------------- a minimal validator


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    reference = node.get("$ref")
    if reference is None:
        return node
    assert reference.startswith("#/$defs/"), reference
    return schema["$defs"][reference.split("/")[-1]]


_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def conformance_problems(
    schema: dict[str, Any], node: dict[str, Any], value: Any, path: str = "$"
) -> list[str]:
    """Every way ``value`` fails ``node``. Empty means it conforms."""
    node = _resolve(schema, node)
    problems: list[str] = []

    if "anyOf" in node:
        for member in node["anyOf"]:
            if not conformance_problems(schema, member, value, path):
                return []
        return [f"{path}: matches no branch of anyOf"]

    declared = node.get("type")
    if declared is not None:
        expected = _JSON_TYPES[declared]
        matches = isinstance(value, expected)
        if declared != "boolean" and isinstance(value, bool):
            matches = False  # a bool is not an integer here
        if not matches:
            return [f"{path}: expected {declared}, got {type(value).__name__}"]

    if "enum" in node and value not in node["enum"]:
        problems.append(f"{path}: {value!r} is not one of {node['enum']}")

    if "pattern" in node and isinstance(value, str):
        if re.match(node["pattern"], value) is None:
            problems.append(f"{path}: {value!r} does not match {node['pattern']}")

    if isinstance(value, dict):
        properties = node.get("properties", {})
        for name in node.get("required", ()):
            if name not in value:
                problems.append(f"{path}: required property '{name}' is absent")
        extra_schema = node.get("additionalProperties")
        for name, item in value.items():
            if name in properties:
                problems += conformance_problems(
                    schema, properties[name], item, f"{path}.{name}"
                )
            elif extra_schema is False:
                problems.append(f"{path}: property '{name}' is not permitted")
            elif isinstance(extra_schema, dict):
                problems += conformance_problems(
                    schema, extra_schema, item, f"{path}.{name}"
                )

    if isinstance(value, list) and isinstance(node.get("items"), dict):
        for index, item in enumerate(value):
            problems += conformance_problems(
                schema, node["items"], item, f"{path}[{index}]"
            )

    return problems


def test_the_conformance_check_would_catch_a_violation(schema, payloads):
    """A check that cannot fail proves nothing."""
    payload = dict(payloads["atomic_matched"])
    assert conformance_problems(schema, schema, payload) == []

    broken = dict(payload, strength=0.75)  # a binary float, not a decimal string
    assert conformance_problems(schema, schema, broken)

    broken = dict(payload, state="ENTERED")  # not a HELIOS state
    assert conformance_problems(schema, schema, broken)

    broken = dict(payload, invented_field="x")  # closed world
    assert conformance_problems(schema, schema, broken)

    broken = dict(payload)
    del broken["strength"]  # canonical output never omits a key
    assert conformance_problems(schema, schema, broken)

    broken = dict(payload, last_evaluated_at_utc="2026-01-06 00:00:00")
    assert conformance_problems(schema, schema, broken)


# ------------------------------------------------------------------ the schema


def test_the_checked_in_schema_matches_the_model(repo_root):
    """A drifted schema would silently misdescribe what HELIOS publishes."""
    checked_in = (repo_root / SCHEMA_PATH).read_text(encoding="utf-8")
    assert checked_in == render_schema(), (
        "docs/schema/strategy_state.schema.json is stale; "
        "regenerate with: python3 -m helios.publish.schema"
    )


def test_the_schema_describes_exactly_the_envelope_fields(schema):
    assert set(schema["properties"]) == set(ENVELOPE_FIELDS)


def test_every_field_is_required_because_canonical_output_omits_nothing(schema):
    assert set(schema["required"]) == set(ENVELOPE_FIELDS)


def test_the_schema_is_a_closed_world(schema):
    """``extra="forbid"`` on the model must be visible to a validator."""
    assert schema["additionalProperties"] is False
    for name, definition in schema["$defs"].items():
        assert definition["additionalProperties"] is False, name
        assert set(definition["required"]) == set(definition["properties"]), name


def test_the_schema_names_the_version_it_describes(schema):
    assert ENVELOPE_SCHEMA_VERSION in schema["description"]
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert schema["properties"]["schema_version"]["type"] == "string"


def test_the_schema_says_decimals_are_strings_not_numbers(schema):
    """Publishing a JSON number here would invite binary float error."""
    branches = schema["properties"]["strength"]["anyOf"]
    types = {branch.get("type") for branch in branches}
    assert types == {"string", "null"}
    decimal = next(branch for branch in branches if branch.get("type") == "string")
    assert decimal["pattern"] == CANONICAL_DECIMAL_PATTERN
    assert "binary floating point" in decimal["description"]


def test_the_schema_pins_the_exact_instant_format(schema):
    field = schema["properties"]["last_evaluated_at_utc"]
    assert field["pattern"] == CANONICAL_INSTANT_PATTERN
    assert field["format"] == "date-time"


def test_no_number_type_survives_anywhere_in_the_schema(schema):
    """Every numeric value HELIOS publishes is an integer count or a string."""
    found: list[str] = []

    def walk(node, path="$"):
        if isinstance(node, dict):
            if node.get("type") == "number":
                found.append(path)
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(schema)
    assert found == []


def test_the_schema_carries_no_pydantic_default(schema):
    """A published payload always states its value; nothing is defaulted."""
    found: list[str] = []

    def walk(node, path="$"):
        if isinstance(node, dict):
            if "default" in node:
                found.append(path)
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(schema)
    assert found == []


def test_the_schema_declares_no_cer_owned_identity_field(schema):
    from helios.contracts import CER_OWNED_IDENTITY_FIELDS

    for name in CER_OWNED_IDENTITY_FIELDS:
        assert name not in schema["properties"]
        for definition in schema["$defs"].values():
            assert name not in definition["properties"]


# ------------------------------------------------ the schema is actually true


@pytest.mark.parametrize(
    "golden_id", ["atomic_matched", "atomic_no_match", "chain_matched", "chain_expired"]
)
def test_every_golden_payload_conforms_to_the_published_schema(
    schema, payloads, golden_id
):
    assert conformance_problems(schema, schema, payloads[golden_id]) == []


def test_the_checked_in_schema_is_the_one_that_validates(repo_root, payloads):
    """Read the document from disk, exactly as an integrator would."""
    on_disk = json.loads((repo_root / SCHEMA_PATH).read_text(encoding="utf-8"))
    for golden_id, payload in payloads.items():
        assert conformance_problems(on_disk, on_disk, payload) == [], golden_id
