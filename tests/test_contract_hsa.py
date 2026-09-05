"""The HSA boundary: HSA authors strategy packages, HELIOS runs them.

Two things are demonstrated here, and the second matters more than the first:

* a realistic HSA-authored handoff loads and resolves completely;
* an under-specified or unresolvable handoff is refused, **precisely** — the
  error names the file, the chain and the exact identity HELIOS could not
  resolve, so HSA fixes a definition instead of HELIOS inventing one.
"""

from __future__ import annotations

import pytest

from helios.contracts import FACT_FIELDS, Timeframe
from helios.errors import StrategySpecError
from helios.integration.hsa_boundary import (
    StrategyBundle,
    describe_handoff,
    load_handoff,
    package_identity,
    resolve_handoff,
)
from helios.spec import PackageKind, load_strategy_package, load_strategy_packages


@pytest.fixture(scope="module")
def hsa_root(request):
    return request.config.rootpath / "fixtures" / "hsa"


@pytest.fixture(scope="module")
def bundle(hsa_root) -> StrategyBundle:
    return load_handoff(hsa_root / "handoff")


# ------------------------------------------------- a realistic handoff loads


def test_a_realistic_hsa_handoff_loads_and_resolves(bundle):
    assert bundle.identities == (
        "gold_context_trigger@1.0.0",
        "gold_staged_sequence@1.0.0",
        "golden_cross@1.0.0",
        "range_breakout@1.0.0",
        "rejection_wick@1.0.0",
        "swing_proximity@1.0.0",
    )
    assert len(bundle.atomics) == 4
    assert len(bundle.chains) == 2


def test_every_checked_in_valid_package_set_resolves_as_a_handoff(request):
    """The checked-in "valid" fixture set must be a genuinely valid handoff.

    Loading a package proves it parses. It does NOT prove the set is coherent:
    a chain naming a component nobody checked in loads perfectly and then
    cannot run. This directory shipped in exactly that state — a SEQUENCE chain
    named ``swing_proximity@1.0.0`` while the directory held no such package —
    so the resolver, not the loader, is what guards it now.
    """
    valid_dir = request.config.rootpath / "fixtures" / "strategy_packages" / "valid"
    bundle = load_handoff(valid_dir)
    chains = bundle.chains
    assert chains, "the valid fixture set must contain at least one chain to resolve"
    for chain_package in chains:
        assert chain_package.chain is not None
        for component in chain_package.chain.components:
            held = bundle.get(f"{component.strategy_id}@{component.strategy_version}")
            assert held.kind is PackageKind.ATOMIC


def test_every_chain_component_resolves_to_an_atomic_package_that_is_present(bundle):
    for chain_package in bundle.chains:
        assert chain_package.chain is not None
        for component in chain_package.chain.components:
            held = bundle.get(f"{component.strategy_id}@{component.strategy_version}")
            assert held.kind is PackageKind.ATOMIC


def test_the_handoff_carries_the_gold_template_without_hard_coding_it(bundle):
    """The role-to-timeframe mapping lives in the package, never in HELIOS."""
    sequence = bundle.get("gold_staged_sequence@1.0.0")
    assert sequence.role_timeframes == {
        "CONTEXT": Timeframe.H4,
        "LOCATION": Timeframe.H1,
        "CONFIRMATION": Timeframe.M15,
        "TRIGGER": Timeframe.M5,
    }
    context_trigger = bundle.get("gold_context_trigger@1.0.0")
    assert set(context_trigger.role_timeframes) == {"CONTEXT", "TRIGGER"}


def test_the_handoff_covers_both_chain_shapes_the_pid_names(bundle):
    primitives = {
        package.chain.primitive.value for package in bundle.chains if package.chain
    }
    assert primitives == {"SEQUENCE", "CONTEXT_TRIGGER"}


def test_every_required_fact_is_one_hermes_publishes(bundle):
    for package in bundle.packages.values():
        for requirement in package.inputs:
            for name in requirement.required_fields:
                assert name in FACT_FIELDS


def test_helios_reports_back_what_it_accepted(bundle):
    """The confirmation HSA gets: identities, role bindings, resolved components."""
    described = describe_handoff(bundle)
    assert described["package_schema_version"] == "helios.strategy_package/1.0.0"
    assert {item["identity"] for item in described["atomic_strategies"]} == {
        "golden_cross@1.0.0",
        "range_breakout@1.0.0",
        "rejection_wick@1.0.0",
        "swing_proximity@1.0.0",
    }
    sequence = next(
        item
        for item in described["chains"]
        if item["identity"] == "gold_staged_sequence@1.0.0"
    )
    assert sequence["primitive"] == "SEQUENCE"
    assert sequence["components"] == [
        "golden_cross@1.0.0",
        "swing_proximity@1.0.0",
        "rejection_wick@1.0.0",
        "range_breakout@1.0.0",
    ]
    assert sequence["role_timeframes"]["CONTEXT"] == "H4"


def test_a_bundle_is_an_immutable_view_over_declarations(bundle):
    with pytest.raises(Exception):
        bundle.packages["injected"] = None  # type: ignore[index]
    with pytest.raises(Exception):
        bundle.packages = {}  # type: ignore[misc]


def test_a_bundle_stores_no_result_history_or_evidence(bundle):
    """HELIOS is not an evidence store; a bundle holds definitions only."""
    import dataclasses

    assert {field.name for field in dataclasses.fields(StrategyBundle)} == {"packages"}
    for name in ("experiment_id", "run_id", "evidence_id", "artifact_id"):
        assert not hasattr(bundle, name)


def test_asking_for_something_the_handoff_does_not_hold_fails_loudly(bundle):
    with pytest.raises(StrategySpecError) as caught:
        bundle.get("no_such_strategy@1.0.0")
    assert "no_such_strategy@1.0.0" in str(caught.value)


# ------------------------------------------- an under-specified package refused


UNDERSPECIFIED_CASES = {
    "sequence_without_ordering_window.chain.yaml": (
        "must declare ordering_window_seconds"
    ),
    "requires_unpublished_fact.atomic.yaml": (
        "HELIOS will not invent a fact HERMES does not publish"
    ),
}


@pytest.mark.parametrize(("name", "expected"), sorted(UNDERSPECIFIED_CASES.items()))
def test_an_under_specified_package_is_refused_precisely(hsa_root, name, expected):
    """HELIOS returns the ambiguity to HSA rather than choosing a default."""
    with pytest.raises(StrategySpecError) as caught:
        load_strategy_package(hsa_root / "underspecified" / name)
    message = str(caught.value)
    assert expected in message
    assert name in message  # the failure names the offending file


def test_every_under_specified_fixture_is_covered(hsa_root):
    on_disk = {
        path.name
        for path in (hsa_root / "underspecified").iterdir()
        if path.is_file()
    }
    assert on_disk == set(UNDERSPECIFIED_CASES)


def test_the_refusal_says_exactly_which_fact_is_unresolved(hsa_root):
    with pytest.raises(StrategySpecError) as caught:
        load_strategy_package(
            hsa_root / "underspecified" / "requires_unpublished_fact.atomic.yaml"
        )
    assert caught.value.context["unknown"] == ["ema_100"]
    assert "ema_50" in caught.value.context["known"]


# ---------------------------------------------- an unresolvable handoff refused


UNRESOLVED_CASES = {
    "missing_component": "no package for strategy_id 'range_breakout' at any version",
    "version_mismatch": "will not substitute a different version",
    "chain_of_chains": "chain-of-chain composition needs explicit architecture authority",
}


@pytest.mark.parametrize(("case", "expected"), sorted(UNRESOLVED_CASES.items()))
def test_an_unresolvable_handoff_is_refused(hsa_root, case, expected):
    with pytest.raises(StrategySpecError) as caught:
        load_handoff(hsa_root / "unresolved" / case)
    unresolved = caught.value.context["unresolved"]
    assert len(unresolved) == 1
    assert expected in unresolved[0]


def test_every_unresolved_case_is_covered(hsa_root):
    on_disk = {
        path.name for path in (hsa_root / "unresolved").iterdir() if path.is_dir()
    }
    assert on_disk == set(UNRESOLVED_CASES)


def test_a_missing_component_names_the_chain_that_needed_it(hsa_root):
    with pytest.raises(StrategySpecError) as caught:
        load_handoff(hsa_root / "unresolved" / "missing_component")
    statement = caught.value.context["unresolved"][0]
    assert "gold_context_trigger@1.0.0" in statement
    assert "range_breakout@1.0.0" in statement


def test_a_version_mismatch_says_what_helios_actually_holds(hsa_root):
    """A version is not a hint: HELIOS refuses rather than binding to a near miss."""
    with pytest.raises(StrategySpecError) as caught:
        load_handoff(hsa_root / "unresolved" / "version_mismatch")
    statement = caught.value.context["unresolved"][0]
    assert "golden_cross@2.0.0" in statement
    assert "['1.0.0']" in statement


def test_a_chain_naming_another_chain_is_refused(hsa_root):
    """v1 chains consume atomic strategies directly, per the PID."""
    with pytest.raises(StrategySpecError) as caught:
        load_handoff(hsa_root / "unresolved" / "chain_of_chains")
    statement = caught.value.context["unresolved"][0]
    assert "gold_outer@1.0.0" in statement
    assert "is itself a CHAIN" in statement


def test_every_unresolved_reference_is_reported_at_once(hsa_root):
    """HSA fixes one handoff, not one fault per attempt."""
    packages = load_strategy_packages(hsa_root / "unresolved" / "missing_component")
    packages.pop("golden_cross@1.0.0")
    with pytest.raises(StrategySpecError) as caught:
        resolve_handoff(packages)
    unresolved = caught.value.context["unresolved"]
    assert len(unresolved) == 2
    assert unresolved == sorted(unresolved)


def test_a_handoff_may_be_resolved_from_values_rather_than_a_directory(hsa_root):
    packages = load_strategy_packages(hsa_root / "handoff")
    resolved = resolve_handoff(list(packages.values()))
    assert resolved.identities == tuple(sorted(packages))


def test_two_definitions_claiming_one_promoted_version_are_refused(hsa_root):
    """Published state would stop being reproducible."""
    packages = list(load_strategy_packages(hsa_root / "handoff").values())
    with pytest.raises(StrategySpecError) as caught:
        resolve_handoff(packages + [packages[0]])
    assert "duplicate strategy identity" in str(caught.value)


def test_a_mislabelled_handoff_key_is_refused(hsa_root):
    packages = load_strategy_packages(hsa_root / "handoff")
    package = packages["golden_cross@1.0.0"]
    with pytest.raises(StrategySpecError) as caught:
        resolve_handoff({"golden_cross@9.9.9": package})
    assert "does not match the package it holds" in str(caught.value)


def test_package_identity_is_the_canonical_key(bundle):
    for key, package in bundle.packages.items():
        assert package_identity(package) == key
