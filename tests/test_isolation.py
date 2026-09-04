"""Strategy isolation and failure containment, enforced rather than trusted.

The PID's invariants: one strategy cannot mutate another's state, one failed
evaluation cannot corrupt unrelated state, and no strategy knows another
exists. Discipline is not evidence, so each of those is checked mechanically:

* the atom source tree is parsed and every import is inspected;
* one atom is imported in a FRESH interpreter and the loaded module set is
  examined, which catches an accidental coupling an import list alone would
  miss;
* a deliberately-raising strategy is evaluated alongside real ones and the
  siblings' published bytes are compared against a run without it;
* the concurrent path is forced to overlap with a barrier, so it is genuinely
  concurrent rather than accidentally sequential.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from helios.contracts import (
    Direction,
    MarketFactWindow,
    StrategyState,
    StrategyStateEnvelope,
)
from helios.contracts.identity import StrategyIdentity
from decimal import Decimal

from pydantic import ValidationError

from helios.errors import ContractViolationError
from helios.protocols import EvaluationContext, RequiredInput, StrategyEvaluator
from helios.spec import parse_strategy_package
from helios.strategies import (
    FAILURE_KEY,
    AtomRegistry,
    evaluate_concurrently,
    evaluate_sequentially,
    shared_facts_context,
)
from helios.strategies.base import AtomicStrategy, AtomReading, AtomVerdict
from helios.strategies.catalogue import ATOM_TYPES, default_registry
from tests.conftest import REPO_ROOT, utc
from tests.test_atom_support import PACKAGE_PATHS, fixture_window, package_for

ATOM_DIRECTORY = REPO_ROOT / "helios" / "strategies" / "atoms"

#: What an atom module is allowed to import. The framework and the contracts,
#: nothing else — and in particular no registry, no catalogue and no sibling.
PERMITTED_IMPORT_PREFIXES = (
    "__future__",
    "decimal",
    "typing",
    "helios.contracts",
    "helios.errors",
    "helios.protocols",
    "helios.spec",
    "helios.strategies.base",
)


def atom_modules() -> list[Path]:
    return sorted(
        path
        for path in ATOM_DIRECTORY.glob("*.py")
        if path.name != "__init__.py"
    )


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(f"{path} uses a relative import")
            if node.module:
                found.add(node.module)
    return found


# ------------------------------------------------------- structural isolation


def test_there_are_six_proof_atoms_and_no_more():
    """The PID says explicitly not to build a large strategy library."""
    assert len(atom_modules()) == 6
    assert len(ATOM_TYPES) == 6
    assert {path.stem for path in atom_modules()} == set(PACKAGE_PATHS)


def test_no_atom_imports_another_atom():
    names = {path.stem for path in atom_modules()}
    for path in atom_modules():
        for module in imported_modules(path):
            if module.startswith("helios.strategies.atoms"):
                raise AssertionError(f"{path.name} imports the atom package: {module}")
            assert module.split(".")[-1] not in names - {path.stem}, (
                f"{path.name} imports {module}"
            )


def test_atom_imports_are_confined_to_the_framework_and_contracts():
    for path in atom_modules():
        for module in imported_modules(path):
            assert module.startswith(PERMITTED_IMPORT_PREFIXES), (
                f"{path.name} imports {module}"
            )


def test_no_atom_mentions_another_atoms_name_anywhere_in_its_source():
    """Not even in a string or a comment: an atom does not know they exist."""
    names = {path.stem for path in atom_modules()}
    for path in atom_modules():
        text = path.read_text(encoding="utf-8")
        for other in names - {path.stem}:
            assert other not in text, f"{path.name} mentions {other}"


def test_the_atom_package_re_exports_nothing():
    """A convenience re-export would couple every atom to every other."""
    source = (ATOM_DIRECTORY / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert all(isinstance(node, ast.Expr) for node in tree.body), (
        "helios/strategies/atoms/__init__.py must contain only its docstring"
    )


@pytest.mark.parametrize("module_name", [path.stem for path in atom_modules()])
def test_importing_one_atom_does_not_load_its_siblings(module_name):
    """The real proof: a fresh interpreter, and no sibling in sys.modules."""
    probe = (
        "import importlib, json, sys;"
        f"importlib.import_module('helios.strategies.atoms.{module_name}');"
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name.startswith('helios.strategies.atoms.'))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    loaded = set(__import__("json").loads(completed.stdout.strip().splitlines()[-1]))
    assert loaded == {f"helios.strategies.atoms.{module_name}"}


def test_a_strategy_is_handed_no_reference_to_any_other():
    """The reading an atom sees carries facts, parameters and its own state."""
    import dataclasses

    fields = {field.name for field in dataclasses.fields(AtomReading)}
    assert fields == {
        "frames",
        "window",
        "parameters",
        "evaluated_at_utc",
        "previous_state",
        "previous_direction",
        "previous_strength",
        "previous_evidence",
    }


# ------------------------------------------------------------ shared facts


#: One instant at which the H4, M15 and M5 fixtures are all fresh: sixty
#: seconds after each of their latest closed bars. The H1 fixture covers the
#: previous day, so no single instant makes it fresh alongside the others;
#: swing_proximity therefore has its own replay tests and is covered here by
#: the structural checks above, which do include all six atoms.
SHARED_INSTANT = "2026-01-06T16:01:00Z"

SHARED_FIXTURES = {
    "H4": "xau_usd_h4",
    "M15": "xau_usd_m15",
    "M5": "xau_usd_m5",
}


def _atoms_over_one_instrument(registry: AtomRegistry | None = None):
    """Every atom whose reference package binds a timeframe in the shared set."""
    registry = registry or default_registry()
    return [
        registry.build(package_for(name))
        for name in sorted(PACKAGE_PATHS)
        if registry.build(package_for(name)).timeframe.code in SHARED_FIXTURES
    ]


def _windows_for(atoms):
    """A frozen window per role, truncated to the bars that had closed."""
    instant = utc(SHARED_INSTANT)
    windows = {}
    for atom in atoms:
        _, window = fixture_window(SHARED_FIXTURES[atom.timeframe.code])
        closed = tuple(
            frame for frame in window.frames if frame.close_time_utc <= instant
        )
        windows[atom.role] = MarketFactWindow(closed)
    return windows


def _context_factory(atoms, policy, previous=None):
    fixture, _ = fixture_window("xau_usd_m5")
    return shared_facts_context(
        instrument=fixture.instrument,
        evaluated_at_utc=utc(SHARED_INSTANT),
        windows=_windows_for(atoms),
        freshness_policy=policy,
        previous=previous,
    )


def test_many_strategies_evaluate_concurrently_over_the_same_facts(policy):
    atoms = _atoms_over_one_instrument()
    outcomes = evaluate_concurrently(atoms, _context_factory(atoms, policy))
    assert len(atoms) == 5
    assert len(outcomes) == len(atoms)
    assert [outcome.identity.canonical for outcome in outcomes] == [
        atom.identity.canonical for atom in atoms
    ]
    assert not any(outcome.contained for outcome in outcomes)
    # Distinct conditions over the same facts reach distinct states.
    assert len({outcome.envelope.state for outcome in outcomes}) > 1
    # Every strategy published against the same instrument and instant.
    assert {str(outcome.envelope.instrument) for outcome in outcomes} == {"XAU_USD"}
    assert len({outcome.envelope.last_evaluated_at_utc for outcome in outcomes}) == 1


def test_the_concurrent_path_actually_overlaps(policy):
    """A barrier that only releases when every worker has arrived.

    If the pool ran the strategies one after another this would time out and
    every outcome would be contained, so the assertion below is a real proof of
    concurrency rather than a description of it.
    """
    atoms = _atoms_over_one_instrument()
    barrier = threading.Barrier(len(atoms), timeout=10)

    class Gate(AtomicStrategy):
        def assess(self, reading: AtomReading) -> AtomVerdict:
            barrier.wait()
            return AtomVerdict(
                holds=False,
                direction=Direction.NEUTRAL,
                explanation="waited for every sibling to arrive",
            )

    gated = [
        Gate(package=atom.package, parameters=atom.parameters) for atom in atoms
    ]
    outcomes = evaluate_concurrently(
        gated, _context_factory(gated, policy), max_workers=len(gated)
    )
    assert not any(outcome.contained for outcome in outcomes)
    assert all(
        outcome.envelope.state is StrategyState.DORMANT for outcome in outcomes
    )


def test_one_strategy_cannot_mutate_the_facts_another_will_read():
    windows = _windows_for(_atoms_over_one_instrument())
    window = next(iter(windows.values()))
    with pytest.raises(TypeError):
        window.frames[0] = window.frames[-1]  # type: ignore[index]
    with pytest.raises(ValidationError):
        window.frames[0].candle.close = Decimal(1)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        window.frames[0].indicators.ema_50 = Decimal(1)  # type: ignore[misc]


def test_a_published_envelope_cannot_be_edited_by_whoever_holds_it(policy):
    atoms = _atoms_over_one_instrument()
    outcomes = evaluate_concurrently(atoms, _context_factory(atoms, policy))
    envelope = outcomes[0].envelope
    with pytest.raises(ValidationError):
        envelope.state = StrategyState.MATCHED  # type: ignore[misc]
    with pytest.raises(ContractViolationError):
        envelope.evidence["injected"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        envelope.inputs[0] = None  # type: ignore[index]


# ------------------------------------------------------------- containment


FAILING_PACKAGE = {
    "schema_version": "helios.strategy_package/1.0.0",
    "kind": "ATOMIC",
    "identity": {"strategy_id": "deliberate_failure", "strategy_version": "1.0.0"},
    "metadata": {
        "title": "Deliberate failure",
        "description": "A strategy that always raises, used to prove containment.",
        "authored_by": "FORGE",
        "authored_at_utc": "2026-01-02T09:30:00Z",
    },
    "inputs": [
        {"role": "CONTEXT", "timeframe": "H4", "lookback": 2,
         "required_fields": ["close"]}
    ],
    "parameters": {},
    "direction": {"mode": "DIRECTIONAL", "resolution": "STRATEGY_LOCAL"},
    "timing": {"evaluate_on": "CLOSED_FRAME"},
    "persistence": {"min_matched_frames": 1, "weakening_enabled": False},
    "expiry": {"mode": "NEVER"},
}


class DeliberateFailureAtom(AtomicStrategy):
    """Raises on every evaluation. Exists only to be contained."""

    ATOM_NAME = "deliberate_failure"
    REQUIRED_FIELDS = ("close",)
    MIN_LOOKBACK = 2
    SUMMARY = "Always raises."

    def assess(self, reading: AtomReading) -> AtomVerdict:
        raise ZeroDivisionError("this strategy is broken on purpose")


def _registry_with_failure() -> AtomRegistry:
    registry = default_registry()
    registry.register(DeliberateFailureAtom)
    return registry


def test_a_raising_strategy_resolves_to_an_explicit_invalid_envelope(policy):
    registry = _registry_with_failure()
    broken = registry.build(parse_strategy_package(FAILING_PACKAGE, origin="test"))
    outcomes = evaluate_concurrently([broken], _context_factory([broken], policy))
    outcome = outcomes[0]
    assert outcome.contained
    assert outcome.failure_type == "ZeroDivisionError"
    assert "broken on purpose" in (outcome.failure_detail or "")
    envelope = outcome.envelope
    assert envelope.state is StrategyState.INVALID
    assert envelope.evidence[FAILURE_KEY] == "ZeroDivisionError"
    assert "broken on purpose" in envelope.explanation
    assert envelope.validity.reason is not None
    assert str(envelope.strategy_id) == "deliberate_failure"


def test_a_raising_strategy_does_not_disturb_a_single_sibling(policy):
    """The siblings' published bytes are identical with and without it."""
    registry = _registry_with_failure()
    healthy = _atoms_over_one_instrument(registry)
    broken = registry.build(parse_strategy_package(FAILING_PACKAGE, origin="test"))

    alone = evaluate_concurrently(healthy, _context_factory(healthy, policy))
    mixed = evaluate_concurrently(
        [broken] + healthy + [broken_twin(registry)],
        _context_factory(healthy, policy),
    )

    assert [outcome.envelope.to_canonical_json() for outcome in alone] == [
        outcome.envelope.to_canonical_json()
        for outcome in mixed
        if not outcome.contained
    ]
    assert sum(1 for outcome in mixed if outcome.contained) == 2


def broken_twin(registry: AtomRegistry):
    document = dict(FAILING_PACKAGE)
    document["identity"] = {
        "strategy_id": "deliberate_failure", "strategy_version": "2.0.0"
    }
    return registry.build(parse_strategy_package(document, origin="test twin"))


def test_containment_preserves_the_history_of_what_just_ended(policy):
    """An INVALID published for a failure still shows what was live before."""
    registry = _registry_with_failure()
    broken = registry.build(parse_strategy_package(FAILING_PACKAGE, origin="test"))
    fixture, window = fixture_window("xau_usd_h4")
    live = StrategyStateEnvelope(
        kind="ATOMIC",
        strategy_id="deliberate_failure",
        strategy_version="1.0.0",
        instrument=fixture.instrument,
        timeframe="H4",
        semantic_role="CONTEXT",
        state=StrategyState.ACTIVE,
        direction=Direction.LONG,
        first_matched_at_utc="2026-01-06T04:00:00Z",
        last_matched_at_utc="2026-01-06T16:00:00Z",
        active_since_utc="2026-01-06T04:00:00Z",
        last_evaluated_at_utc="2026-01-06T16:00:00Z",
        validity={},
        inputs=(),
    )
    outcomes = evaluate_concurrently(
        [broken],
        _context_factory([broken], policy, previous={"deliberate_failure@1.0.0": live}),
    )
    envelope = outcomes[0].envelope
    assert envelope.state is StrategyState.INVALID
    assert envelope.direction is Direction.LONG
    assert envelope.first_matched_at_utc == live.first_matched_at_utc
    assert envelope.active_since_utc is None


def test_a_strategy_is_only_ever_handed_its_own_previous_envelope(policy):
    """The context factory keys previous state by identity and nothing else."""
    registry = _registry_with_failure()
    atoms = _atoms_over_one_instrument(registry)
    first = evaluate_concurrently(atoms, _context_factory(atoms, policy))
    published = {
        outcome.identity.canonical: outcome.envelope for outcome in first
    }
    factory = _context_factory(atoms, policy, previous=published)
    for atom in atoms:
        context = factory(atom)
        assert context.previous is published[atom.identity.canonical]
    second = evaluate_concurrently(atoms, factory)
    assert not any(outcome.contained for outcome in second)


def test_the_same_identity_cannot_be_evaluated_twice_in_one_pass(policy):
    atoms = _atoms_over_one_instrument()
    with pytest.raises(ValueError):
        evaluate_sequentially(atoms + atoms[:1], _context_factory(atoms, policy))


class BareEvaluator:
    """A minimal StrategyEvaluator that is not an AtomicStrategy at all."""

    def __init__(self, identity: StrategyIdentity, declared: RequiredInput) -> None:
        self._identity = identity
        self._declared = declared

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    def required_inputs(self) -> tuple[RequiredInput, ...]:
        return (self._declared,)

    def evaluate(self, context: EvaluationContext) -> Any:
        raise RuntimeError("a bare evaluator that cannot evaluate")


def test_containment_covers_anything_implementing_the_interface(policy):
    """Containment is a property of the evaluation path, not of the base class."""
    atoms = _atoms_over_one_instrument()
    reference = atoms[0]
    bare = BareEvaluator(
        StrategyIdentity("bare_evaluator", "1.0.0"), reference.required_inputs()[0]
    )
    assert isinstance(bare, StrategyEvaluator)
    outcomes = evaluate_concurrently(
        list(atoms) + [bare], _context_factory(atoms, policy)
    )
    contained = [outcome for outcome in outcomes if outcome.contained]
    assert len(contained) == 1
    assert contained[0].failure_type == "RuntimeError"
    assert contained[0].envelope.state is StrategyState.INVALID
