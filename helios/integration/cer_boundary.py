"""The CER boundary — identity compatibility, and nothing more.

CER owns durable empirical evidence. HELIOS does not, must not, and here does
not: this module creates no experiment, run, evidence or artifact record, no
table, no store and no registry. It contains pure functions over values HELIOS
already publishes.

What HELIOS owes CER is narrow and precise:

* it **originates** ``strategy_id`` and ``strategy_version`` and carries them,
  unchanged, on every envelope it publishes — atomic and chain alike;
* it **never mints or publishes** ``experiment_id``, ``run_id``,
  ``evidence_id`` or ``artifact_id``. Those are CER's, and a guard test
  asserts they never appear in a HELIOS payload;
* a promoted ``(strategy_id, strategy_version)`` pair is **immutable**. A
  logic change is a new version, never an in-place edit — otherwise evidence
  CER recorded against a version would silently stop describing the strategy
  that produced it.

The third point is the one that is easy to assert and hard to prove.
:func:`definition_fingerprint` makes it mechanical: the fingerprint is
computed from the package's own declared content, so "did the definition
change?" is answered by the definitions themselves rather than by whoever is
promoting them.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from helios.contracts.identity import (
    CER_IDENTITY_FIELDS,
    CER_OWNED_IDENTITY_FIELDS,
    HELIOS_ORIGINATED_IDENTITY_FIELDS,
    StrategyIdentity,
    assert_version_immutable,
)
from helios.contracts.output import StrategyStateEnvelope
from helios.contracts.serialisation import canonical_dumps, canonicalise
from helios.errors import ContractViolationError, IdentityError
from helios.spec.model import StrategyPackage

__all__ = [
    "CER_IDENTITY_FIELDS",
    "CER_OWNED_IDENTITY_FIELDS",
    "FINGERPRINT_EXCLUDED",
    "HELIOS_ORIGINATED_IDENTITY_FIELDS",
    "assert_promotion_immutable",
    "assert_publishes_no_cer_owned_field",
    "canonical_identity",
    "definition_fingerprint",
    "published_identity",
    "strategy_identity_of",
]

#: Package content deliberately left OUT of the definition fingerprint.
#:
#: ``metadata`` is the human-facing title, description, author and authoring
#: instant. Correcting a typo in a description is not a logic change and must
#: not force a version bump; changing a threshold, an input, a role binding, a
#: chain component or an expiry rule is, and does.
#:
#: ``identity.strategy_version`` is excluded because the fingerprint's whole
#: job is to be compared *across* versions.
FINGERPRINT_EXCLUDED: tuple[str, ...] = ("metadata", "identity.strategy_version")


def published_identity(envelope: StrategyStateEnvelope) -> Mapping[str, str]:
    """The exact identity projection CER keys against, from a published state.

    Returns only the two fields HELIOS originates. A chain publishes its own
    identity here, exactly as an atomic strategy does, so CER never branches
    on ``kind`` to learn who produced a state.
    """
    if not isinstance(envelope, StrategyStateEnvelope):
        raise ContractViolationError(
            "a published StrategyStateEnvelope is required",
            received_type=type(envelope).__name__,
        )
    return {
        "strategy_id": str(envelope.strategy_id),
        "strategy_version": str(envelope.strategy_version),
    }


def strategy_identity_of(envelope: StrategyStateEnvelope) -> StrategyIdentity:
    """The published identity as the immutable value type."""
    fields = published_identity(envelope)
    return StrategyIdentity(fields["strategy_id"], fields["strategy_version"])


def canonical_identity(envelope: StrategyStateEnvelope) -> str:
    """``golden_cross@1.0.0`` — the unambiguous single-string identity."""
    return strategy_identity_of(envelope).canonical


def assert_publishes_no_cer_owned_field(payload: Mapping[str, Any]) -> None:
    """Refuse a payload that carries an identifier CER owns.

    Usable from either side of the boundary. A HELIOS payload containing
    ``run_id`` would mean HELIOS had started minting evidence identity, which
    is exactly the competing store the PID forbids.
    """
    if not isinstance(payload, Mapping):
        raise ContractViolationError(
            "expected a published payload mapping",
            received_type=type(payload).__name__,
        )
    present = sorted(name for name in CER_OWNED_IDENTITY_FIELDS if name in payload)
    if present:
        raise IdentityError(
            "a HELIOS payload carries identity fields CER owns; HELIOS "
            "publishes runtime strategy state and never mints evidence identity",
            offending_fields=present,
            cer_owned=list(CER_OWNED_IDENTITY_FIELDS),
        )


def definition_fingerprint(package: StrategyPackage) -> str:
    """A deterministic fingerprint of a strategy package's *definition*.

    Computed over the package's canonical JSON with :data:`FINGERPRINT_EXCLUDED`
    removed, so two packages fingerprint the same exactly when they declare the
    same behaviour. The canonical form already guarantees sorted keys, exact
    decimals and no float representation, so the fingerprint is stable across
    processes and hosts.

    This is a pure function of the value handed to it. Nothing is stored.
    """
    if not isinstance(package, StrategyPackage):
        raise ContractViolationError(
            "a StrategyPackage is required to fingerprint a definition",
            received_type=type(package).__name__,
        )
    document = canonicalise(package)
    if not isinstance(document, dict):  # pragma: no cover - canonicalise contract
        raise ContractViolationError("a strategy package must canonicalise to an object")
    document.pop("metadata", None)
    identity = document.get("identity")
    if isinstance(identity, dict):
        identity = dict(identity)
        identity.pop("strategy_version", None)
        document["identity"] = identity
    return hashlib.sha256(canonical_dumps(document).encode("utf-8")).hexdigest()


def assert_promotion_immutable(
    promoted: StrategyPackage, proposed: StrategyPackage
) -> None:
    """Enforce the promotion rule between two package definitions.

    Raises :class:`~helios.errors.IdentityError` when a changed definition
    reuses a promoted version — the silent mutation CER cannot survive — and
    equally when an unchanged definition bumps one, because a version that
    moves for no reason makes CER's record of *which* version produced a
    result meaningless in the other direction.
    """
    changed = definition_fingerprint(promoted) != definition_fingerprint(proposed)
    assert_version_immutable(
        StrategyIdentity(
            promoted.identity.strategy_id, promoted.identity.strategy_version
        ),
        StrategyIdentity(
            proposed.identity.strategy_id, proposed.identity.strategy_version
        ),
        definition_changed=changed,
    )
