"""JSON Schema export for HSA.

HSA emits strategy packages; this is the machine-readable target it emits
against. The generated document is checked in at
``docs/schema/strategy_package.schema.json`` and a test fails if the checked-in
copy drifts from the model, so the published schema can never quietly stop
describing what HELIOS actually accepts.

**Why this module does more than call pydantic.** Most of what makes a package
malformed is a rule BETWEEN fields — a ``SEQUENCE`` chain without an ordering
window, an ``ATOMIC`` package that also declares a chain, an expiry mode
without its accompanying value. Those live in ``@model_validator`` methods,
which produce no JSON Schema at all, so the generated document described the
shape of a package and almost none of its rules: measured against the checked-in
malformed fixtures, a real Draft 2020-12 validator rejected two of ten. An HSA
author targeting the published artefact therefore got a clean pass on packages
HELIOS refuses — the artefact was handing out false assurance.

:func:`_stated_rules` restates those rules as JSON Schema, derived from the
SAME constants the validators use so the two cannot drift apart in vocabulary.
Python remains authoritative: two of the model's rules have no Draft 2020-12
expression at all — see :data:`PYTHON_ONLY_RULES` — and the schema says so in
its own description rather than leaving the gap silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helios.contracts.market_fact import FACT_FIELDS
from helios.spec.model import (
    STRATEGY_PACKAGE_SCHEMA_VERSION,
    ChainPrimitive,
    DirectionResolution,
    ExpiryMode,
    PackageKind,
    StrategyPackage,
)

#: Where the generated schema is checked in, relative to the repository root.
SCHEMA_PATH = Path("docs/schema/strategy_package.schema.json")

#: Rules HELIOS enforces that Draft 2020-12 cannot express, named so the gap is
#: stated rather than silent. Both require comparing one member of a document
#: against another member chosen at validation time, which JSON Schema has no
#: construct for:
#:
#: * a semantic role bound to more than one ``inputs`` entry — uniqueness BY A
#:   KEY, where ``uniqueItems`` compares whole items and roles are an open
#:   pattern rather than a closed enum, so they cannot be enumerated either;
#: * a parameter whose ``value`` falls outside the ``minimum``/``maximum`` that
#:   same parameter declares — a comparison between sibling members.
#:
#: A package passing this schema is therefore necessary but not sufficient;
#: loading it through HELIOS remains the authority.
PYTHON_ONLY_RULES: tuple[str, ...] = (
    "a semantic role may be bound to at most one entry in 'inputs'",
    "a parameter's 'value' must lie within the 'minimum'/'maximum' it declares",
)

_POSITIVE_INTEGER = {"type": "integer", "minimum": 1}
_NOT_DECLARED = {"type": "null"}


def _component_with_role(role: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"role": {"const": role}},
        "required": ["role"],
    }


def _stated_rules() -> list[dict[str, Any]]:
    """The model's cross-field rules, restated as JSON Schema.

    Every literal below comes from the enums and constants the validators
    themselves use, so widening the vocabulary in one place cannot leave the
    published schema describing the old one.
    """
    atomic, chain_kind = PackageKind.ATOMIC.value, PackageKind.CHAIN.value
    return [
        # helios.spec.model.StrategyPackage._package_is_coherent
        {
            "if": {
                "properties": {"kind": {"const": atomic}},
                "required": ["kind"],
            },
            "then": {
                "properties": {
                    "chain": _NOT_DECLARED,
                    "inputs": {"minItems": 1},
                    "direction": {
                        "properties": {
                            "resolution": {
                                "const": DirectionResolution.STRATEGY_LOCAL.value
                            }
                        }
                    },
                }
            },
        },
        {
            "if": {
                "properties": {"kind": {"const": chain_kind}},
                "required": ["kind"],
            },
            "then": {
                "properties": {
                    "chain": {"type": "object"},
                    "direction": {
                        "properties": {
                            "resolution": {
                                "const": DirectionResolution.FROM_COMPONENTS.value
                            }
                        }
                    },
                },
                "required": ["chain"],
            },
        },
    ]


def _chain_rules() -> list[dict[str, Any]]:
    """helios.spec.model.ChainSpec._primitive_rules_hold, as JSON Schema."""
    sequence = ChainPrimitive.SEQUENCE.value
    ordered_component = {
        "type": "object",
        "properties": {"sequence_index": {"type": "integer", "minimum": 0}},
        "required": ["sequence_index"],
    }
    unordered_component = {
        "type": "object",
        "properties": {"sequence_index": _NOT_DECLARED},
    }
    return [
        {
            "if": {
                "properties": {"primitive": {"const": sequence}},
                "required": ["primitive"],
            },
            "then": {
                # "in order, within what period?" has no safe default.
                "properties": {
                    "ordering_window_seconds": dict(_POSITIVE_INTEGER),
                    "components": {"items": ordered_component},
                },
                "required": ["ordering_window_seconds"],
            },
            "else": {
                # An ordering on an unordered primitive states two
                # contradictory things about the same chain.
                "properties": {
                    "ordering_window_seconds": _NOT_DECLARED,
                    "components": {"items": unordered_component},
                }
            },
        },
        {
            "if": {
                "properties": {
                    "primitive": {"const": ChainPrimitive.CONTEXT_TRIGGER.value}
                },
                "required": ["primitive"],
            },
            "then": {
                "properties": {
                    "components": {
                        "allOf": [
                            {
                                "contains": _component_with_role(role),
                                "minContains": 1,
                                "maxContains": 1,
                            }
                            for role in ("CONTEXT", "TRIGGER")
                        ]
                    }
                }
            },
        },
    ]


def _expiry_rules() -> list[dict[str, Any]]:
    """helios.spec.model.ExpirySpec._mode_matches_its_value, as JSON Schema."""
    rules: list[dict[str, Any]] = []
    for mode, present, absent in (
        (ExpiryMode.FRAMES.value, "frames", "duration_seconds"),
        (ExpiryMode.DURATION.value, "duration_seconds", "frames"),
    ):
        rules.append(
            {
                "if": {"properties": {"mode": {"const": mode}}, "required": ["mode"]},
                "then": {
                    "properties": {
                        present: dict(_POSITIVE_INTEGER),
                        absent: _NOT_DECLARED,
                    },
                    "required": [present],
                },
            }
        )
    rules.append(
        {
            "if": {
                "properties": {"mode": {"const": ExpiryMode.NEVER.value}},
                "required": ["mode"],
            },
            "then": {
                "properties": {
                    "frames": _NOT_DECLARED,
                    "duration_seconds": _NOT_DECLARED,
                }
            },
        }
    )
    return rules


def _tighten(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the per-field and cross-field constraints pydantic does not emit."""
    defs = schema["$defs"]

    # A package declaring an unfamiliar format is refused, not guessed at.
    schema["properties"]["schema_version"]["const"] = STRATEGY_PACKAGE_SCHEMA_VERSION

    field = defs["InputRequirement"]["properties"]
    field["required_fields"].update(
        {"minItems": 1, "uniqueItems": True, "items": {"enum": sorted(FACT_FIELDS)}}
    )
    field["lookback"]["minimum"] = 1
    field["max_age_seconds"]["anyOf"][0]["minimum"] = 1

    defs["PersistenceSpec"]["properties"]["min_matched_frames"]["minimum"] = 1

    component = defs["ChainComponent"]["properties"]
    component["required_states"].update({"minItems": 1, "uniqueItems": True})
    component["sequence_index"]["anyOf"][0]["minimum"] = 0

    for name in ("title", "description", "authored_by"):
        defs["PackageMetadata"]["properties"][name]["minLength"] = 1

    # A chain combines at least two components, or it is not a composition.
    defs["ChainSpec"]["properties"]["components"]["minItems"] = 2

    defs["ChainSpec"]["allOf"] = _chain_rules()
    defs["ExpirySpec"]["allOf"] = _expiry_rules()
    schema["allOf"] = _stated_rules()
    return schema


def strategy_package_json_schema() -> dict[str, Any]:
    """The JSON Schema describing a HELIOS strategy package."""
    schema = StrategyPackage.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "HELIOS strategy package"
    schema["description"] = (
        "Declarative strategy/chain definition consumed by HELIOS and emitted "
        f"by HSA. Package schema_version: {STRATEGY_PACKAGE_SCHEMA_VERSION}. "
        "Passing this schema is necessary but not sufficient: HELIOS also "
        "enforces rules Draft 2020-12 cannot express — "
        + "; ".join(PYTHON_ONLY_RULES)
        + ". Load the package through HELIOS to check those."
    )
    return _tighten(schema)


def render_schema() -> str:
    """The exact text of the checked-in schema document."""
    return json.dumps(strategy_package_json_schema(), indent=2, sort_keys=True) + "\n"


def write_schema(root: Path | str = ".") -> Path:
    """(Re)generate the checked-in schema document."""
    target = Path(root) / SCHEMA_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_schema(), encoding="utf-8")
    return target


if __name__ == "__main__":  # pragma: no cover - operator utility
    print(write_schema())
