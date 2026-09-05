"""The atomic strategies this build ships, and the reference packages for them.

This module is the ONLY place that names all six atoms together. It is kept
separate from :mod:`helios.strategies.registry` and is deliberately not
imported by :mod:`helios.strategies` itself, because importing one atom must
not drag its siblings into the process — that independence is a property
``tests/test_isolation.py`` verifies in a fresh interpreter, and it would be
destroyed by a convenient re-export.

``REFERENCE_PACKAGE_DIRECTORY`` is package DATA shipped alongside the code, not
runtime configuration: it holds reference strategy definitions for the atoms
that have no package elsewhere in the repository. A deployment loads whatever
package directory it is configured with; nothing here decides that.
"""

from __future__ import annotations

from pathlib import Path

from helios.strategies.atoms.golden_cross import GoldenCrossAtom
from helios.strategies.atoms.momentum_volatility import MomentumVolatilityAtom
from helios.strategies.atoms.no_wick_candle import NoWickCandleAtom
from helios.strategies.atoms.range_breakout import RangeBreakoutAtom
from helios.strategies.atoms.rejection_wick import RejectionWickAtom
from helios.strategies.atoms.swing_proximity import SwingProximityAtom
from helios.strategies.base import AtomicStrategy
from helios.strategies.registry import AtomRegistry

#: Reference strategy packages shipped with the code.
REFERENCE_PACKAGE_DIRECTORY = Path(__file__).resolve().parent / "packages"

#: Every atomic strategy this build can evaluate, ordered by name so that
#: enumerating the catalogue is itself deterministic.
ATOM_TYPES: tuple[type[AtomicStrategy], ...] = tuple(
    sorted(
        (
            GoldenCrossAtom,
            MomentumVolatilityAtom,
            NoWickCandleAtom,
            RangeBreakoutAtom,
            RejectionWickAtom,
            SwingProximityAtom,
        ),
        key=lambda atom_type: atom_type.ATOM_NAME,
    )
)


def default_registry() -> AtomRegistry:
    """A registry holding every atomic strategy this build ships."""
    registry = AtomRegistry()
    for atom_type in ATOM_TYPES:
        registry.register(atom_type)
    return registry
