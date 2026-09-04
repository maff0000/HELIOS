"""JSON Schema export for HSA.

HSA emits strategy packages; this is the machine-readable target it emits
against. The generated document is checked in at
``docs/schema/strategy_package.schema.json`` and a test fails if the checked-in
copy drifts from the model, so the published schema can never quietly stop
describing what HELIOS actually accepts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helios.spec.model import STRATEGY_PACKAGE_SCHEMA_VERSION, StrategyPackage

#: Where the generated schema is checked in, relative to the repository root.
SCHEMA_PATH = Path("docs/schema/strategy_package.schema.json")


def strategy_package_json_schema() -> dict[str, Any]:
    """The JSON Schema describing a HELIOS strategy package."""
    schema = StrategyPackage.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "HELIOS strategy package"
    schema["description"] = (
        "Declarative strategy/chain definition consumed by HELIOS and emitted "
        f"by HSA. Package schema_version: {STRATEGY_PACKAGE_SCHEMA_VERSION}."
    )
    return schema


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
