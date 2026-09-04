"""Shared helpers for the atomic strategy tests, and a check that they are sane.

The helpers here do one thing the strategy tests all need: replay a fixture
bar by bar, feeding each evaluation the envelope the previous one published,
exactly as a running engine would. Every expected state sequence in the atom
tests was worked out by hand from ``docs/FIXTURES.md`` first and asserted here
second.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import pytest
import yaml

from helios.contracts import MarketFactWindow, StrategyState, Timeframe
from helios.contracts.window import MarketFactWindow as _Window
from helios.hermes import load_fixture
from helios.protocols import EvaluationContext
from helios.spec import load_strategy_package, parse_strategy_package
from helios.spec.loader import _DecimalSafeLoader
from helios.spec.model import StrategyPackage
from helios.strategies.base import AtomicStrategy
from helios.strategies.catalogue import REFERENCE_PACKAGE_DIRECTORY, default_registry
from tests.conftest import REPO_ROOT

CANONICAL_FIXTURES = REPO_ROOT / "fixtures" / "hermes" / "xau_usd"
STALE_FIXTURES = REPO_ROOT / "fixtures" / "hermes" / "stale"
HSA_PACKAGES = REPO_ROOT / "fixtures" / "strategy_packages" / "valid"

#: Where each proof atom's reference package lives. Three were authored with
#: the contract kernel and live with the other HSA fixtures; the three that
#: had none ship beside the code.
PACKAGE_PATHS: Mapping[str, Path] = {
    "golden_cross": HSA_PACKAGES / "golden_cross.atomic.yaml",
    "range_breakout": HSA_PACKAGES / "breakout.atomic.yaml",
    "rejection_wick": HSA_PACKAGES / "rejection_wick.atomic.json",
    "swing_proximity": REFERENCE_PACKAGE_DIRECTORY / "swing_proximity.atomic.yaml",
    "no_wick_candle": REFERENCE_PACKAGE_DIRECTORY / "no_wick_candle.atomic.yaml",
    "momentum_volatility": REFERENCE_PACKAGE_DIRECTORY / "momentum_volatility.atomic.yaml",
}


def package_for(atom_name: str) -> StrategyPackage:
    """The reference package for one proof atom."""
    return load_strategy_package(PACKAGE_PATHS[atom_name])


def read_package_document(path: Path) -> dict[str, Any]:
    """The raw package document, with numbers still exact decimals."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        from decimal import Decimal

        return json.loads(text, parse_float=Decimal)
    return yaml.load(text, Loader=_DecimalSafeLoader)


def package_variant(atom_name: str, **changes: Any) -> StrategyPackage:
    """A parameterised variant of a reference package.

    ``changes`` are dotted paths into the package document. A variant that
    changes the definition MUST also change the version: a promoted
    ``(strategy_id, strategy_version)`` is immutable, and a test that quietly
    redefined 1.0.0 would be modelling something HELIOS forbids.
    """
    document = read_package_document(PACKAGE_PATHS[atom_name])
    for dotted, value in changes.items():
        node: Any = document
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        node[parts[-1]] = value
    if changes and document["identity"]["strategy_version"] == "1.0.0":
        raise AssertionError(
            "a package variant must declare a new strategy_version; a promoted "
            "version is immutable"
        )
    return parse_strategy_package(document, origin=f"variant of {atom_name}")


def build(package: StrategyPackage) -> AtomicStrategy:
    """Bind a package to its implementation through the real registry."""
    return default_registry().build(package)


def fixture_window(name: str, *, stale: bool = False) -> tuple[Any, MarketFactWindow]:
    path = (STALE_FIXTURES if stale else CANONICAL_FIXTURES) / f"{name}.json"
    fixture = load_fixture(path)
    return fixture, fixture.window


def replay(
    atom: AtomicStrategy,
    fixture_name: str,
    policy,
    *,
    stale: bool = False,
    limit: Optional[int] = None,
):
    """Evaluate a fixture bar by bar, feeding each result forward.

    Each evaluation happens at the instant its bar closed and sees only the
    history up to that bar — which is what makes the sequence a replay rather
    than a series of independent look-ups.
    """
    fixture, window = fixture_window(fixture_name, stale=stale)
    frames = window.frames
    lookback = atom.required_inputs()[0].lookback
    previous = None
    results = []
    stop = len(frames) if limit is None else min(len(frames), limit)
    for index in range(lookback - 1, stop):
        context = EvaluationContext(
            instrument=fixture.instrument,
            evaluated_at_utc=frames[index].close_time_utc,
            windows={atom.role: _Window(frames[: index + 1])},
            parameters=atom.parameters,
            freshness_policy=policy,
            previous=previous,
        )
        envelope = atom.evaluate(context)
        results.append((index, envelope))
        previous = envelope
    return results


def states(results: Iterable[tuple[int, Any]]) -> list[str]:
    return [envelope.state.value for _, envelope in results]


def directions(results: Iterable[tuple[int, Any]]) -> list[str]:
    return [envelope.direction.value for _, envelope in results]


def at(results: Sequence[tuple[int, Any]], frame_index: int):
    """The envelope published for one fixture frame."""
    for index, envelope in results:
        if index == frame_index:
            return envelope
    raise AssertionError(f"frame {frame_index} was not evaluated in this replay")


def context_for(
    atom: AtomicStrategy,
    window: MarketFactWindow,
    policy,
    *,
    instrument,
    evaluated_at_utc,
    previous=None,
) -> EvaluationContext:
    return EvaluationContext(
        instrument=instrument,
        evaluated_at_utc=evaluated_at_utc,
        windows={atom.role: window},
        parameters=atom.parameters,
        freshness_policy=policy,
        previous=previous,
    )


# ------------------------------------------------------- the helpers' own test


def test_the_replay_harness_feeds_state_forward(policy):
    """A harness that silently dropped the previous envelope would prove nothing."""
    atom = build(package_for("golden_cross"))
    results = replay(atom, "xau_usd_h4", policy)
    assert [index for index, _ in results] == list(range(1, 12))
    # ACTIVE is only reachable from a previous live envelope, so its presence
    # is proof the harness carried state between evaluations.
    assert StrategyState.ACTIVE.value in states(results)
    # Each evaluation happens at its own bar's close, in order.
    instants = [envelope.last_evaluated_at_utc for _, envelope in results]
    assert instants == sorted(instants)
    assert len(set(instants)) == len(instants)


def test_every_proof_atom_has_a_reference_package():
    registry = default_registry()
    assert set(PACKAGE_PATHS) == set(registry.registered)
    for name, path in PACKAGE_PATHS.items():
        package = load_strategy_package(path)
        assert str(package.identity.strategy_id) == name
        assert path.is_file()


def test_a_variant_must_bump_the_version():
    with pytest.raises(AssertionError):
        package_variant("golden_cross", **{"parameters.min_separation.value": 1})


def test_reference_packages_bind_their_declared_timeframes():
    """Every atom's role/timeframe comes from its package, never from source."""
    bindings = {
        name: (str(build(package_for(name)).role), build(package_for(name)).timeframe)
        for name in PACKAGE_PATHS
    }
    assert bindings["golden_cross"] == ("CONTEXT", Timeframe.H4)
    assert bindings["swing_proximity"] == ("LOCATION", Timeframe.H1)
    assert bindings["rejection_wick"] == ("CONFIRMATION", Timeframe.M15)
    assert bindings["range_breakout"] == ("TRIGGER", Timeframe.M5)
