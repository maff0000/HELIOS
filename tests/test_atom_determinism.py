"""Determinism: identical ordered inputs must produce identical published bytes.

This is the invariant the whole engine rests on. It is checked three ways:
repeating one evaluation, repeating a whole replay, and comparing the
concurrent evaluation path against the sequential one — because a concurrency
bug that reordered results would be invisible to the first two.

A separate process is also used, so nothing about the result can depend on
hash seeding, dictionary insertion order or an ambient decimal context this
process happened to be left in.
"""

from __future__ import annotations

import decimal
import json
import subprocess
import sys
from decimal import Decimal

import pytest

from helios.contracts import StrategyStateEnvelope
from tests.conftest import REPO_ROOT
from tests.test_atom_support import PACKAGE_PATHS, build, package_for, replay
from tests.test_isolation import _atoms_over_one_instrument, _context_factory
from helios.strategies import evaluate_concurrently, evaluate_sequentially

FIXTURE_FOR = {
    "golden_cross": "xau_usd_h4",
    "range_breakout": "xau_usd_m5",
    "rejection_wick": "xau_usd_m15",
    "no_wick_candle": "xau_usd_m15",
    "momentum_volatility": "xau_usd_m5",
    "swing_proximity": "xau_usd_h1",
}


def canonical_replay(atom_name: str, policy) -> list[str]:
    atom = build(package_for(atom_name))
    return [
        envelope.to_canonical_json()
        for _, envelope in replay(atom, FIXTURE_FOR[atom_name], policy)
    ]


@pytest.mark.parametrize("atom_name", sorted(PACKAGE_PATHS))
def test_a_replay_repeats_byte_for_byte(atom_name, policy):
    first = canonical_replay(atom_name, policy)
    second = canonical_replay(atom_name, policy)
    assert first == second
    assert first, "the replay produced no envelopes to compare"


@pytest.mark.parametrize("atom_name", sorted(PACKAGE_PATHS))
def test_one_evaluation_repeats_byte_for_byte(atom_name, policy):
    """A freshly bound strategy is indistinguishable from a reused one."""
    published = {canonical_replay(atom_name, policy)[-1] for _ in range(5)}
    assert len(published) == 1


@pytest.mark.parametrize("atom_name", sorted(PACKAGE_PATHS))
def test_published_envelopes_round_trip_through_canonical_json(atom_name, policy):
    for text in canonical_replay(atom_name, policy):
        parsed = StrategyStateEnvelope.from_canonical_json(text)
        assert parsed.to_canonical_json() == text


@pytest.mark.parametrize("atom_name", sorted(PACKAGE_PATHS))
def test_no_binary_float_reaches_the_published_bytes(atom_name, policy):
    """Every number is exact decimal text, so no platform float repr can leak."""
    for text in canonical_replay(atom_name, policy):
        document = json.loads(text, parse_float=_reject_float)
        assert document["schema_version"] == "helios.strategy_state/1.0.0"


def _reject_float(text: str):
    raise AssertionError(f"published output contains a non-integer JSON number: {text}")


def test_the_concurrent_path_publishes_exactly_what_the_sequential_one_does(policy):
    atoms = _atoms_over_one_instrument()
    factory = _context_factory(atoms, policy)
    sequential = evaluate_sequentially(atoms, factory)
    for _ in range(5):
        concurrent = evaluate_concurrently(atoms, factory, max_workers=len(atoms))
        assert [outcome.identity.canonical for outcome in concurrent] == [
            outcome.identity.canonical for outcome in sequential
        ]
        assert [outcome.envelope.to_canonical_json() for outcome in concurrent] == [
            outcome.envelope.to_canonical_json() for outcome in sequential
        ]


def test_results_follow_the_supplied_order_not_the_completion_order(policy):
    atoms = _atoms_over_one_instrument()
    forwards = evaluate_concurrently(atoms, _context_factory(atoms, policy))
    backwards = evaluate_concurrently(
        list(reversed(atoms)), _context_factory(atoms, policy)
    )
    assert [outcome.identity.canonical for outcome in forwards] == list(
        reversed([outcome.identity.canonical for outcome in backwards])
    )
    by_identity = {
        outcome.identity.canonical: outcome.envelope.to_canonical_json()
        for outcome in backwards
    }
    for outcome in forwards:
        assert by_identity[outcome.identity.canonical] == (
            outcome.envelope.to_canonical_json()
        )


def test_an_ambient_decimal_context_cannot_change_a_derived_measure(policy):
    """Every ratio an atom derives is quantised inside a fixed context.

    A caller that had installed a coarser or finer decimal context must not be
    able to change what a strategy computes, so the quantisation does not
    inherit the ambient context.

    Note the boundary: this asserts the DERIVED VALUES are context-independent.
    Rendering them to canonical JSON is the contract layer's job and carries
    the same guarantee separately — see
    ``tests/test_serialisation.py::test_a_hostile_decimal_context_cannot_change_the_emitted_digits``.
    """
    baseline = [
        dict(envelope.evidence)
        for _, envelope in replay(build(package_for("rejection_wick")), "xau_usd_m15", policy)
    ]
    for precision, rounding in ((4, decimal.ROUND_FLOOR), (60, decimal.ROUND_CEILING)):
        with decimal.localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            observed = [
                dict(envelope.evidence)
                for _, envelope in replay(
                    build(package_for("rejection_wick")), "xau_usd_m15", policy
                )
            ]
        assert observed == baseline
    assert baseline[5]["upper_wick_ratio"] == Decimal("0.846154")


def test_quantised_ratios_ignore_the_installed_context():
    from helios.strategies import RATIO_QUANTUM, quantised_ratio

    expected = quantised_ratio(Decimal("5.50"), Decimal("6.50"))
    assert expected == Decimal("0.846154")
    assert -expected.as_tuple().exponent == -RATIO_QUANTUM.as_tuple().exponent
    for precision in (3, 4, 9, 50):
        with decimal.localcontext() as context:
            context.prec = precision
            assert quantised_ratio(Decimal("5.50"), Decimal("6.50")) == expected


def _published_by_a_separate_process() -> list[list[str]]:
    """Replay the golden cross in fresh interpreters with different hash seeds."""
    probe = r"""
import json, sys
from pathlib import Path
from helios.config import load_config
from helios.contracts import MarketFactWindow
from helios.hermes import load_fixture
from helios.protocols import EvaluationContext
from helios.spec import load_strategy_package
from helios.strategies.catalogue import default_registry

root = Path.cwd()
policy = load_config({}, config_file=root / "tests" / "data" / "helios.test.toml").freshness_policy()
package = load_strategy_package(
    root / "fixtures" / "strategy_packages" / "valid" / "golden_cross.atomic.yaml"
)
atom = default_registry().build(package)
fixture = load_fixture(root / "fixtures" / "hermes" / "xau_usd" / "xau_usd_h4.json")
frames = fixture.frames
previous = None
published = []
for index in range(1, len(frames)):
    context = EvaluationContext(
        instrument=fixture.instrument,
        evaluated_at_utc=frames[index].close_time_utc,
        windows={atom.role: MarketFactWindow(frames[: index + 1])},
        parameters=atom.parameters,
        freshness_policy=policy,
        previous=previous,
    )
    previous = atom.evaluate(context)
    published.append(previous.to_canonical_json())
print(json.dumps(published))
"""
    runs = []
    for seed in ("0", "12345"):
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO_ROOT)},
        )
        runs.append(json.loads(completed.stdout))
    return runs


def test_a_separate_process_publishes_the_same_bytes():
    """Nothing may depend on hash seeding or this process's own state."""
    runs = _published_by_a_separate_process()
    assert runs[0] == runs[1]
    assert len(runs[0]) == 11


def test_the_separate_process_agrees_with_this_one(policy):
    outside = _published_by_a_separate_process()
    assert outside[0] == canonical_replay("golden_cross", policy)


def test_evidence_decimals_are_exact_not_rounded(policy):
    """0.846154 is the quantised wick ratio, published as exact decimal text."""
    envelopes = canonical_replay("rejection_wick", policy)
    matched = [json.loads(text) for text in envelopes]
    ratios = [
        item["evidence"]["upper_wick_ratio"] for item in matched
    ]
    assert "0.846154" in ratios
    assert all(isinstance(value, str) for value in ratios)
    assert Decimal(ratios[5]) == Decimal("0.846154")
