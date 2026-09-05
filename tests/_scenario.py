"""Building the vertical-slice deployment the way a real deployment is built.

Every test that needs the whole slice — input, atoms, chains, publication —
assembles it through :func:`helios.runtime.service.build_runtime` from
configuration, exactly as ``python3 -m helios.runtime`` does. Nothing here
constructs an engine by hand or supplies a value HELIOS would not have been
given, because a harness that assembles the system differently from the way it
actually runs proves something other than the system.

Not collected by pytest: the leading underscore keeps it a helper, like
``tests/_scan.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from helios.config import load_config
from helios.publish import InMemorySink, StatePublisher
from helios.runtime.service import StrategyRuntime, build_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The time-aligned market facts: one instrument, four timeframes, one clock.
SCENARIO_FEED_DIR = REPO_ROOT / "fixtures" / "hermes" / "scenario"
#: The self-contained HSA handoff those facts are evaluated against.
SCENARIO_PACKAGE_DIR = REPO_ROOT / "fixtures" / "strategy_packages" / "scenario"

#: Every unit the scenario deployment publishes, in publication order:
#: atomic strategies in canonical identity order, then chains in theirs.
SCENARIO_UNITS: tuple[str, ...] = (
    "golden_cross",
    "range_breakout",
    "rejection_wick",
    "swing_proximity",
    "gold_continuous_context_trigger",
    "gold_continuous_sequence",
)

#: The instant every stage of the chain is simultaneously established.
MATCH_INSTANT = "2026-03-02T14:01:00Z"


def scenario_environment(
    status_file: Path, **overrides: str
) -> dict[str, str]:
    """A complete HELIOS environment for the vertical-slice deployment.

    Written out in full rather than layered onto a default, because HELIOS has
    no defaults: a deployment that does not state a setting does not have one.
    """
    environment = {
        "HELIOS_ENVIRONMENT": "test",
        "HELIOS_LOG_LEVEL": "INFO",
        "HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER": "1.5",
        "HELIOS_FRESHNESS_GRACE_SECONDS": "60",
        "HELIOS_FRESHNESS_ALLOW_INCOMPLETE_FRAMES": "false",
        "HELIOS_ACCEPTED_HERMES_SCHEMA_VERSIONS": "hermes.market_fact/1.0.0",
        "HELIOS_PUBLICATION_SINK": "MEMORY",
        "HELIOS_RUNTIME_INSTRUMENT": "XAU_USD",
        "HELIOS_RUNTIME_FEED_DIR": str(SCENARIO_FEED_DIR),
        "HELIOS_STRATEGY_PACKAGE_DIR": str(SCENARIO_PACKAGE_DIR),
        "HELIOS_RUNTIME_TICK_SECONDS": "0",
        "HELIOS_RUNTIME_ON_FEED_END": "STOP",
        "HELIOS_RUNTIME_STATUS_FILE": str(status_file),
        "HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS": "120",
    }
    environment.update(overrides)
    return environment


def build_scenario_runtime(
    status_file: Path,
    *,
    publisher: Optional[StatePublisher] = None,
    **overrides: str,
) -> StrategyRuntime:
    """Assemble the vertical-slice deployment from configuration alone."""
    config = load_config(scenario_environment(status_file, **overrides))
    return build_runtime(
        config,
        publisher=publisher if publisher is not None else StatePublisher(InMemorySink()),
    )


def replay(status_file: Path, **overrides: str) -> tuple[str, ...]:
    """Every payload one full pass over the ordered feed publishes."""
    sink = InMemorySink()
    runtime = build_scenario_runtime(
        status_file, publisher=StatePublisher(sink), **overrides
    )
    for instant in runtime.instants:
        runtime.evaluate_once(instant)
    return sink.records

