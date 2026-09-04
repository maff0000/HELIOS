"""The published JSON Schema for the strategy-state envelope.

FALCON must be able to **validate** what HELIOS publishes, not reverse-engineer
it. This module generates that schema from the model itself and the generated
document is checked in at ``docs/schema/strategy_state.schema.json``; a test
fails if the checked-in copy drifts, so the published schema can never quietly
stop describing what HELIOS actually emits.

Why the schema is derived, not taken verbatim
---------------------------------------------
``StrategyStateEnvelope.model_json_schema()`` describes what the model will
*accept on input*, which is deliberately more permissive than what HELIOS
*emits*. Publishing that document unchanged would mislead an integrator in
four concrete ways:

* a decimal is accepted as a JSON number **or** a string, but canonical output
  always emits an exact decimal **string** — binary floats never appear on the
  wire (see ``docs/CONTRACTS.md`` §3.3);
* an instant is accepted in any ISO-8601 form, but canonical output always
  emits fixed-width ``YYYY-MM-DDTHH:MM:SS.ffffffZ``;
* optional fields look absent-able, but canonical output preserves ``null``
  rather than omitting a key — so **every** field is always present;
* the model is a closed world (``extra="forbid"``), which the generated
  document does not state.

:func:`published_schema` therefore applies exactly those four transformations,
mechanically, to the generated document. The transformations are structural
(they match how pydantic renders a type, not a hand-maintained field list), so
adding a field to the envelope updates the published schema automatically.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from helios.contracts.output import ENVELOPE_SCHEMA_VERSION, StrategyStateEnvelope

#: Where the generated schema is checked in, relative to the repository root.
SCHEMA_PATH = Path("docs/schema/strategy_state.schema.json")

#: Exactly the instant format :func:`helios.clock.to_iso8601_utc` emits.
CANONICAL_INSTANT_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"

#: Exactly the decimal text :func:`helios.contracts.serialisation.canonical_decimal`
#: emits: normalised, exact, never in exponent form, never a binary float.
CANONICAL_DECIMAL_PATTERN = r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$"

_DECIMAL_DESCRIPTION = (
    "An exact decimal, published as a normalised string. Never a JSON number: "
    "binary floating point cannot represent these values exactly."
)
_INSTANT_DESCRIPTION = "A UTC instant, fixed-width ISO-8601 ending in 'Z'."


def _is_number(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "number" and len(node) == 1


def _is_patterned_string(node: Any) -> bool:
    return (
        isinstance(node, dict)
        and node.get("type") == "string"
        and "pattern" in node
        and set(node) <= {"type", "pattern"}
    )


def _is_plain_string(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "string" and set(node) == {"type"}


def _decimal_node(nullable: bool) -> dict[str, Any]:
    decimal = {
        "type": "string",
        "pattern": CANONICAL_DECIMAL_PATTERN,
        "description": _DECIMAL_DESCRIPTION,
    }
    if not nullable:
        return decimal
    return {"anyOf": [decimal, {"type": "null"}]}


def _collapse_union(node: dict[str, Any]) -> dict[str, Any]:
    """Reduce a pydantic ``anyOf`` to what canonical output can actually emit.

    A ``Decimal`` renders as ``number | patterned-string``; canonical output
    only ever emits the string form, so the number branch is removed and the
    pattern is replaced with the exact canonical one. A patterned string
    sitting beside an unconstrained string is then redundant and is dropped.
    """
    raw = list(node["anyOf"])
    # Inspect the GENERATED members before rewriting them: pydantic renders a
    # Decimal as ``number | patterned-string``, and that pair must be
    # recognised in its original form.
    had_number = any(_is_number(member) for member in raw)
    if had_number:
        raw = [
            member
            for member in raw
            if not _is_number(member) and not _is_patterned_string(member)
        ]

    members = [_transform(member) for member in raw]
    nullable = any(member == {"type": "null"} for member in members)
    members = [member for member in members if member != {"type": "null"}]

    if had_number and not any(_is_plain_string(member) for member in members):
        # An unconstrained string would already admit every decimal string; a
        # patterned one is only worth stating when nothing broader is present.
        members.append(_decimal_node(nullable=False))

    deduped: list[dict[str, Any]] = []
    for member in members:
        if member not in deduped:
            deduped.append(member)

    if nullable:
        deduped.append({"type": "null"})
    if len(deduped) == 1:
        return deduped[0]
    return {"anyOf": deduped}


def _transform(node: Any) -> Any:
    """Rewrite one generated schema node into the published, emitted form."""
    if isinstance(node, list):
        return [_transform(item) for item in node]
    if not isinstance(node, dict):
        return node

    node = {key: value for key, value in node.items() if key != "default"}

    if "anyOf" in node:
        collapsed = _collapse_union(node)
        # Keep any sibling keywords the union carried (``title`` and the like)
        # without letting them overwrite what the collapse just decided.
        for key, value in node.items():
            if key != "anyOf" and key not in collapsed:
                collapsed[key] = value
        return collapsed

    if node.get("type") == "number":
        return _decimal_node(nullable=False)

    if node.get("type") == "string" and node.get("format") == "date-time":
        return {
            "type": "string",
            "format": "date-time",
            "pattern": CANONICAL_INSTANT_PATTERN,
            "description": _INSTANT_DESCRIPTION,
        }

    result = {key: _transform(value) for key, value in node.items()}

    if isinstance(result.get("properties"), dict):
        # Canonical output preserves nulls rather than omitting keys, so every
        # declared property is always present, and nothing else ever is.
        result["required"] = sorted(result["properties"])
        result["additionalProperties"] = False
    return result


def published_schema() -> dict[str, Any]:
    """JSON Schema describing exactly what HELIOS publishes to FALCON."""
    generated = deepcopy(StrategyStateEnvelope.model_json_schema(mode="validation"))
    schema = _transform(generated)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "HELIOS strategy state"
    schema["description"] = (
        "The normalised strategy-state envelope HELIOS publishes for atomic "
        "strategies and chains alike. Canonical JSON: object keys sorted, no "
        "insignificant whitespace, decimals as exact strings, instants as "
        "fixed-width UTC, nulls preserved. Envelope schema_version: "
        f"{ENVELOPE_SCHEMA_VERSION}. A consumer that does not recognise the "
        "published schema_version must refuse the payload rather than "
        "interpret it."
    )
    return schema


def render_schema() -> str:
    """The exact text of the checked-in schema document."""
    return json.dumps(published_schema(), indent=2, sort_keys=True) + "\n"


def write_schema(root: Path | str = ".") -> Path:
    """(Re)generate the checked-in schema document."""
    target = Path(root) / SCHEMA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_schema(), encoding="utf-8")
    return target


if __name__ == "__main__":  # pragma: no cover - operator utility
    print(write_schema())
