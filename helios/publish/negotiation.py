"""Schema-version negotiation for published strategy state.

A published envelope carries ``schema_version``. A consumer that does not
recognise the value **must refuse the payload** rather than interpret it
partially — that is the whole reason the field is published, and it is the one
piece of behaviour a FALCON integrator has to implement correctly.

This module is the reference implementation of that rule, usable from both
sides of the boundary.

The rule
--------

1. ``schema_version`` is ``<namespace>/<major>.<minor>.<patch>``. HELIOS
   publishes exactly one namespace, ``helios.strategy_state``.

2. A consumer declares the exact set of versions it understands. Anything
   else is refused. HELIOS deliberately does **not** ship a "same major is
   probably fine" rule: guessing at an unfamiliar payload is precisely the
   reverse-engineering the PID forbids, and a consumer that wants leniency
   must state which versions it accepts, deliberately.

3. Refusal happens on the declared version alone, **before** the payload is
   interpreted. :func:`read_schema_version` reads only that one field, so a
   consumer never has to parse a document whose shape it does not know.

4. HELIOS bumps the version deliberately:

   * **major** — a field is removed or renamed, a field's meaning changes, or
     an accepted value set narrows;
   * **minor** — a field is added, or an accepted value set widens;
   * **patch** — the published bytes are unchanged and only documentation
     changed.

   Because the envelope is a closed world (unknown fields are rejected on
   read), even an additive change is visible to a strict consumer. That is
   intended: an unexpected field is a loud upgrade decision, never a silent
   pass-through.

5. HELIOS refuses in both directions. It will not construct, publish or parse
   an envelope carrying a version this build does not implement.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from helios.contracts._fields import validate_model
from helios.contracts.output import ENVELOPE_SCHEMA_VERSION, StrategyStateEnvelope
from helios.contracts.serialisation import canonical_loads
from helios.errors import ContractViolationError

#: The namespace HELIOS publishes strategy state under.
ENVELOPE_SCHEMA_NAMESPACE = "helios.strategy_state"

#: Every envelope schema version this build can produce and read. A consumer
#: pinning HELIOS may copy this tuple as its own accepted set.
SUPPORTED_ENVELOPE_SCHEMA_VERSIONS: tuple[str, ...] = (ENVELOPE_SCHEMA_VERSION,)

#: The single field a consumer must read before anything else.
SCHEMA_VERSION_FIELD = "schema_version"


def schema_namespace(schema_version: str) -> Optional[str]:
    """The namespace part of a schema version, or ``None`` if it is unshaped."""
    if not isinstance(schema_version, str) or schema_version.count("/") != 1:
        return None
    namespace, _, version = schema_version.partition("/")
    return namespace if namespace and version else None


def read_schema_version(payload: str | Mapping[str, Any]) -> str:
    """Read the declared ``schema_version`` and nothing else.

    Accepts canonical JSON text or an already-parsed mapping. A payload that
    does not declare a version cannot be negotiated at all and is refused: a
    consumer must never fall back to "assume it is the version I know".
    """
    if isinstance(payload, str):
        document = canonical_loads(payload)
    else:
        document = payload
    if not isinstance(document, Mapping):
        raise ContractViolationError(
            "a published strategy-state payload must be a JSON object",
            received_type=type(document).__name__,
        )
    declared = document.get(SCHEMA_VERSION_FIELD)
    if not isinstance(declared, str) or not declared.strip():
        raise ContractViolationError(
            "published payload does not declare a schema_version; a consumer "
            "cannot negotiate a contract it cannot identify",
            field=SCHEMA_VERSION_FIELD,
        )
    return declared


def negotiate_schema_version(
    declared: str, accepted: Iterable[str] = SUPPORTED_ENVELOPE_SCHEMA_VERSIONS
) -> str:
    """Return ``declared`` if the consumer accepts it, else refuse loudly.

    The error names what arrived, what is accepted, and whether the payload at
    least came from the HELIOS strategy-state namespace — enough for an
    operator to tell "HELIOS moved on without me" apart from "something else
    entirely is writing to my sink".
    """
    accepted_versions = tuple(accepted)
    if not accepted_versions:
        raise ContractViolationError(
            "a consumer must declare at least one accepted schema_version; an "
            "empty set cannot accept anything"
        )
    if declared in accepted_versions:
        return declared
    raise ContractViolationError(
        "unrecognised strategy-state schema_version; the consumer must refuse "
        "this payload rather than interpret it",
        received=declared,
        accepted=sorted(accepted_versions),
        same_namespace=schema_namespace(declared) == ENVELOPE_SCHEMA_NAMESPACE,
    )


def accept_payload(
    payload: str | Mapping[str, Any],
    *,
    accepted_schema_versions: Iterable[str] = SUPPORTED_ENVELOPE_SCHEMA_VERSIONS,
) -> StrategyStateEnvelope:
    """The consumer-side read path: negotiate first, interpret second.

    This is the function a FALCON integrator should mirror. Note the order —
    the version is checked before the document is validated, so an unfamiliar
    payload is refused on its own terms instead of producing a pile of
    confusing field errors.
    """
    document = canonical_loads(payload) if isinstance(payload, str) else payload
    declared = read_schema_version(document)
    negotiate_schema_version(declared, accepted_schema_versions)
    return validate_model(
        StrategyStateEnvelope, document, label="StrategyStateEnvelope"
    )
