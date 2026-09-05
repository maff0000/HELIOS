"""The four system boundaries, demonstrated rather than asserted.

HELIOS sits between four systems and owes each of them something different.
This package holds the HELIOS side of each boundary, and
``docs/INTEGRATION.md`` is the one document an integrator on any of them reads.

* :mod:`helios.integration.hermes_boundary` — what HELIOS *consumes*: the
  market-fact contract, the loud acceptance path, and a machine-readable
  statement of exactly which fields are required. A data contract, not a
  client: no hostname, port, credential or connection string exists here.
* :mod:`helios.integration.hsa_boundary` — what HELIOS *runs*: resolving a
  handoff of strategy packages, and refusing precisely rather than inventing
  when a chain names something HELIOS does not hold.
* :mod:`helios.integration.cer_boundary` — identity compatibility only.
  HELIOS originates ``strategy_id`` and ``strategy_version`` and carries them
  cleanly; CER owns everything else, including all durable evidence.
* :mod:`helios.integration.exemplars` — the golden published payloads FALCON
  codes against, checked in under ``fixtures/falcon/``.

The publication path itself — sinks, the schema-version rule and the published
JSON Schema — lives in :mod:`helios.publish`.
"""

from helios.integration.cer_boundary import (
    FINGERPRINT_EXCLUDED,
    assert_promotion_immutable,
    assert_publishes_no_cer_owned_field,
    canonical_identity,
    definition_fingerprint,
    published_identity,
    strategy_identity_of,
)
from helios.integration.exemplars import (
    GOLDEN_DIR,
    GOLDEN_SCHEMA_VERSION,
    GoldenExemplar,
    golden_exemplars,
    read_golden_document,
    write_golden_files,
)
from helios.integration.hermes_boundary import (
    INPUT_REJECTION_RULES,
    PROVENANCE_FIELDS,
    accept_market_facts,
    describe_input_contract,
    input_freshness,
)
from helios.integration.hsa_boundary import (
    StrategyBundle,
    describe_handoff,
    load_handoff,
    package_identity,
    resolve_handoff,
)

__all__ = [
    "FINGERPRINT_EXCLUDED",
    "GOLDEN_DIR",
    "GOLDEN_SCHEMA_VERSION",
    "INPUT_REJECTION_RULES",
    "PROVENANCE_FIELDS",
    "GoldenExemplar",
    "StrategyBundle",
    "accept_market_facts",
    "assert_promotion_immutable",
    "assert_publishes_no_cer_owned_field",
    "canonical_identity",
    "definition_fingerprint",
    "describe_handoff",
    "describe_input_contract",
    "golden_exemplars",
    "input_freshness",
    "load_handoff",
    "package_identity",
    "published_identity",
    "read_golden_document",
    "resolve_handoff",
    "strategy_identity_of",
    "write_golden_files",
]
