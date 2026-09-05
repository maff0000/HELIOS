"""The strategy handoff the running deployment is configured with.

``fixtures/strategy_packages/scenario/`` is a complete, self-contained HSA
handoff: the four atomic definitions the chains name, and two chain definitions
written for **continuous** evaluation. It is what ``python3 -m helios.runtime``
loads, so what is asserted here is a property of the shipped deployment, not of
a test's private arrangement.

Two things earn their own tests.

**The atomic definitions are copies, and copies drift.** A promoted
``(strategy_id, strategy_version)`` is immutable — it is the key CER records
evidence against — so every file in this repository claiming one identity must
describe one strategy. That was already held for the two ``swing_proximity``
copies by name; with a third handoff directory, naming pairs one at a time
stops scaling. The guard below is therefore repository-wide: it groups **every**
package file by identity and holds each group to a single definition
fingerprint.

**The chains are new definitions, not edits.** ``gold_continuous_sequence`` and
``gold_continuous_context_trigger`` differ from the packages they resemble in a
way that changes behaviour — each component is satisfied by ``MATCHED`` *or*
``ACTIVE`` — so they carry new identities. Reusing the promoted ones would have
been exactly the in-place edit ``docs/INTEGRATION.md`` §4.2 forbids, and the
fingerprint check proves the two are genuinely different definitions rather
than a rename.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helios.integration import definition_fingerprint, load_handoff, package_identity
from helios.spec import PackageKind, load_strategy_package, load_strategy_packages
from helios.spec.loader import SUPPORTED_SUFFIXES
from helios.strategies.catalogue import default_registry
from tests._scenario import SCENARIO_PACKAGE_DIR

EXPECTED_IDENTITIES = (
    "gold_continuous_context_trigger@1.0.0",
    "gold_continuous_sequence@1.0.0",
    "golden_cross@1.0.0",
    "range_breakout@1.0.0",
    "rejection_wick@1.0.0",
    "swing_proximity@1.0.0",
)


@pytest.fixture(scope="module")
def bundle():
    return load_handoff(SCENARIO_PACKAGE_DIR)


def test_the_scenario_handoff_resolves_completely(bundle):
    assert bundle.identities == EXPECTED_IDENTITIES
    assert len(bundle.atomics) == 4
    assert len(bundle.chains) == 2
    for chain_package in bundle.chains:
        assert chain_package.chain is not None
        for component in chain_package.chain.components:
            held = bundle.get(f"{component.strategy_id}@{component.strategy_version}")
            assert held.kind is PackageKind.ATOMIC


def test_every_atomic_package_binds_to_an_implementation_this_build_ships(bundle):
    registry = default_registry()
    bound = registry.build_all(bundle.atomics)
    assert [atom.identity.canonical for atom in bound] == [
        "golden_cross@1.0.0",
        "range_breakout@1.0.0",
        "rejection_wick@1.0.0",
        "swing_proximity@1.0.0",
    ]


def test_the_handoff_covers_the_full_gold_template(bundle):
    """CONTEXT->H4, LOCATION->H1, CONFIRMATION->M15, TRIGGER->M5 — declared by
    the packages themselves, which is the only place that mapping lives."""
    sequence = bundle.get("gold_continuous_sequence@1.0.0")
    assert {
        role: timeframe.code for role, timeframe in sequence.role_timeframes.items()
    } == {
        "CONTEXT": "H4",
        "LOCATION": "H1",
        "CONFIRMATION": "M15",
        "TRIGGER": "M5",
    }


def test_the_continuous_chains_accept_a_component_that_is_still_holding(bundle):
    """The one deliberate difference, asserted rather than left to a comment.

    HELIOS re-evaluates every five minutes; a fifteen-minute confirmation that
    matched publishes ACTIVE on the next four evaluations against the same
    still-current bar. A chain accepting only MATCHED could hold for exactly
    one evaluation.
    """
    for identity in (
        "gold_continuous_sequence@1.0.0",
        "gold_continuous_context_trigger@1.0.0",
    ):
        chain = bundle.get(identity).chain
        assert chain is not None
        for component in chain.components:
            states = {state.value for state in component.required_states}
            assert "MATCHED" in states and "ACTIVE" in states, (identity, component)


def test_the_continuous_chains_are_new_definitions_and_not_edits(repo_root):
    """A behaviour change is a new identity; an identity is never edited."""
    valid = repo_root / "fixtures" / "strategy_packages" / "valid"
    pairs = (
        ("gold_continuous_sequence.chain.yaml", "gold_sequence.chain.yaml"),
        (
            "gold_continuous_context_trigger.chain.yaml",
            "gold_context_trigger.chain.yaml",
        ),
    )
    for new_name, promoted_name in pairs:
        new = load_strategy_package(SCENARIO_PACKAGE_DIR / new_name)
        promoted = load_strategy_package(valid / promoted_name)
        assert str(new.identity.strategy_id) != str(promoted.identity.strategy_id)
        assert definition_fingerprint(new) != definition_fingerprint(promoted)


# ------------------------------------------------- repository-wide identity


#: Identities this guard found already carrying several different definitions
#: when it was written, in package sets outside the work item that added it.
#: They are named here so the guard can be repository-wide from the start
#: without either silently blessing the discrepancy or silently repairing
#: fixtures another work item owns. The assertion is one-directional — an
#: identity may leave this list by being repaired, and nothing may join it —
#: so the guard tightens over time and can never be loosened by accident.
#:
#: `fixtures/hsa/handoff/swing_proximity.atomic.yaml` declares parameters
#: `max_distance_atr` and `swing_lookback_bars` where the promoted definition
#: declares `max_distance` and `lookback_bars`, reads an extra market fact, and
#: expires after a different number of frames. `gold_staged_sequence@1.0.0`
#: differs between the same two directories in its LOCATION required_fields.
#: Both are reported to the PL rather than changed here.
KNOWN_UNRESOLVED_IDENTITIES = frozenset(
    {"gold_staged_sequence@1.0.0", "swing_proximity@1.0.0"}
)


def every_package_file(repo_root) -> list[Path]:
    """Every strategy package checked into this repository.

    ``malformed``, ``underspecified`` and ``unresolved`` are excluded because
    those files exist to be REFUSED — an unresolvable handoff deliberately
    holds a definition that does not agree with the promoted one, which is the
    fault the fixture demonstrates.
    """
    excluded = (
        repo_root / "fixtures" / "strategy_packages" / "malformed",
        repo_root / "fixtures" / "hsa" / "underspecified",
        repo_root / "fixtures" / "hsa" / "unresolved",
    )
    found = []
    for root in (repo_root / "fixtures", repo_root / "helios"):
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if any(str(path).startswith(str(directory)) for directory in excluded):
                continue
            if "helios.strategy_package/" in path.read_text(encoding="utf-8"):
                found.append(path)
    return found


def test_the_repository_wide_scan_actually_finds_the_packages(repo_root):
    """A scan that misses the files proves nothing."""
    found = every_package_file(repo_root)
    assert len(found) >= 18, [str(path) for path in found]
    for directory in ("scenario", "valid", "handoff", "packages"):
        assert any(f"/{directory}/" in str(path) for path in found), directory


def definitions_by_identity(repo_root) -> dict[str, dict[str, list[str]]]:
    grouped: dict[str, dict[str, list[str]]] = {}
    for path in every_package_file(repo_root):
        package = load_strategy_package(path)
        grouped.setdefault(package_identity(package), {}).setdefault(
            definition_fingerprint(package), []
        ).append(str(path.relative_to(repo_root)))
    return grouped


def test_one_promoted_identity_means_one_definition_everywhere(repo_root):
    """Every copy of an identity in this repository describes one strategy.

    A promoted ``(strategy_id, strategy_version)`` is what CER records evidence
    against. Two files claiming one version while describing different
    behaviour would make published state non-reproducible, and the fingerprint
    is the mechanical statement of "different behaviour": it hashes the
    declared inputs, parameters, direction, timing, persistence, expiry and
    chain composition, and deliberately excludes metadata.
    """
    grouped = definitions_by_identity(repo_root)
    divergent = {
        identity: groups for identity, groups in grouped.items() if len(groups) > 1
    }
    unexpected = set(divergent) - KNOWN_UNRESOLVED_IDENTITIES
    assert not unexpected, (
        "one identity, several definitions: "
        f"{ {name: divergent[name] for name in sorted(unexpected)} }"
    )


def test_the_identity_guard_is_not_vacuous(repo_root):
    """Several files really do claim one identity, so the check has work to do."""
    grouped = definitions_by_identity(repo_root)
    copied = {
        identity: sorted(paths)
        for identity, groups in grouped.items()
        for paths in groups.values()
        if len(paths) > 1
    }
    assert "swing_proximity@1.0.0" in copied, copied
    assert len(copied["swing_proximity@1.0.0"]) >= 3, copied


def test_every_scenario_package_agrees_with_every_other_copy_of_its_identity(repo_root):
    """The set this work item added introduces no drift of its own."""
    grouped = definitions_by_identity(repo_root)
    for path in sorted(SCENARIO_PACKAGE_DIR.iterdir()):
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        package = load_strategy_package(path)
        groups = grouped[package_identity(package)]
        holding = [
            fingerprint
            for fingerprint, paths in groups.items()
            if str(path.relative_to(repo_root)) in paths
        ]
        assert len(holding) == 1
        others = {
            fingerprint: paths
            for fingerprint, paths in groups.items()
            if fingerprint != holding[0]
        }
        for fingerprint, paths in others.items():
            assert all(
                p.startswith("fixtures/hsa/handoff/") for p in paths
            ), f"{path.name} disagrees with {paths}"


def test_the_scenario_directory_holds_exactly_what_the_handoff_needs(repo_root):
    """A handoff directory is a deployment's configuration, not a junk drawer."""
    held = load_strategy_packages(SCENARIO_PACKAGE_DIR)
    assert tuple(sorted(held)) == EXPECTED_IDENTITIES
