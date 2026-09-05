"""The HSA handoff boundary.

HSA is the strategy-engineering authority. It authors declarative strategy
packages (``docs/CONTRACTS.md`` §5); HELIOS loads and runs them. A handoff is
a *set* of packages, and a set has a property no single package has: whether
HELIOS can actually resolve every chain component to a definition it holds.

The governing rule, from the PID and from HSA's own contract, is that **HELIOS
refuses rather than invents**. That rule has a bite here that it does not have
inside one package:

* a chain naming a component HELIOS does not hold is *unresolved*. HELIOS must
  not run the chain with that component silently omitted, and must not go
  looking for something with a similar name.
* a chain naming ``golden_cross@2.0.0`` when the handoff contains
  ``golden_cross@1.0.0`` is *also* unresolved. Binding to the nearest
  available version would publish state under an identity CER records evidence
  against, produced by a definition nobody approved. A version is not a hint.
* a chain naming another chain is refused: v1 chains consume atomic strategies
  directly, and the PID requires explicit architecture authority before
  chain-of-chain recursion exists.

Every unresolved reference in a handoff is reported at once, naming the chain
that needed it, so HSA fixes one handoff rather than discovering faults one at
a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from helios.errors import StrategySpecError
from helios.spec.loader import load_strategy_packages
from helios.spec.model import PackageKind, StrategyPackage


def package_identity(package: StrategyPackage) -> str:
    """The canonical ``strategy_id@major.minor.patch`` key for a package."""
    return f"{package.identity.strategy_id}@{package.identity.strategy_version}"


@dataclass(frozen=True, slots=True)
class StrategyBundle:
    """A resolved handoff: every package, and every reference satisfied.

    A bundle is a read-only view over declarations. It stores no evaluation
    result, no history and no evidence — CER owns durable empirical evidence
    and HELIOS must not grow a competing store.
    """

    packages: Mapping[str, StrategyPackage]

    def __post_init__(self) -> None:
        object.__setattr__(self, "packages", MappingProxyType(dict(self.packages)))

    @property
    def identities(self) -> tuple[str, ...]:
        return tuple(sorted(self.packages))

    @property
    def atomics(self) -> tuple[StrategyPackage, ...]:
        return tuple(
            self.packages[key]
            for key in self.identities
            if self.packages[key].kind is PackageKind.ATOMIC
        )

    @property
    def chains(self) -> tuple[StrategyPackage, ...]:
        return tuple(
            self.packages[key]
            for key in self.identities
            if self.packages[key].kind is PackageKind.CHAIN
        )

    def get(self, identity: str) -> StrategyPackage:
        """One package by canonical identity, failing loudly if absent."""
        package = self.packages.get(identity)
        if package is None:
            raise StrategySpecError(
                "no strategy package with this identity is in the handoff",
                identity=identity,
                available=self.identities,
            )
        return package


def _versions_of(strategy_id: str, packages: Mapping[str, StrategyPackage]) -> list[str]:
    return sorted(
        str(package.identity.strategy_version)
        for package in packages.values()
        if str(package.identity.strategy_id) == strategy_id
    )


def resolve_handoff(
    packages: Mapping[str, StrategyPackage] | Iterable[StrategyPackage],
) -> StrategyBundle:
    """Confirm HELIOS can run every package in a handoff, or refuse loudly.

    The error's ``unresolved`` context is a sorted list of precise statements:
    which chain needed what, and what HELIOS actually holds for that
    ``strategy_id``. That precision is the requirement — "something is
    missing" would leave HSA guessing, which is how invented trading logic
    gets written.
    """
    if isinstance(packages, Mapping):
        indexed = dict(packages)
    else:
        indexed = {}
        for package in packages:
            key = package_identity(package)
            if key in indexed:
                raise StrategySpecError(
                    "duplicate strategy identity in handoff; two definitions "
                    "claiming one promoted version would make published state "
                    "non-reproducible",
                    identity=key,
                )
            indexed[key] = package

    for key, package in indexed.items():
        expected = package_identity(package)
        if key != expected:
            raise StrategySpecError(
                "handoff key does not match the package it holds",
                key=key,
                package_identity=expected,
            )

    unresolved: list[str] = []
    for chain_package in indexed.values():
        if chain_package.kind is not PackageKind.CHAIN or chain_package.chain is None:
            continue
        chain_key = package_identity(chain_package)
        for component in chain_package.chain.components:
            wanted = f"{component.strategy_id}@{component.strategy_version}"
            held = indexed.get(wanted)
            if held is None:
                available = _versions_of(str(component.strategy_id), indexed)
                if available:
                    unresolved.append(
                        f"chain {chain_key} requires component {wanted}, which is "
                        f"not in the handoff; HELIOS holds "
                        f"{component.strategy_id} at {available} and will not "
                        f"substitute a different version"
                    )
                else:
                    unresolved.append(
                        f"chain {chain_key} requires component {wanted}, and the "
                        f"handoff contains no package for strategy_id "
                        f"'{component.strategy_id}' at any version"
                    )
            elif held.kind is not PackageKind.ATOMIC:
                unresolved.append(
                    f"chain {chain_key} names {wanted}, which is itself a CHAIN; "
                    f"v1 chains consume atomic strategies directly and "
                    f"chain-of-chain composition needs explicit architecture "
                    f"authority"
                )

    if unresolved:
        raise StrategySpecError(
            "HSA handoff has unresolved chain components; HELIOS refuses to run "
            "a chain whose components it cannot identify rather than inventing "
            "or substituting a definition",
            unresolved=sorted(unresolved),
            available=sorted(indexed),
        )
    return StrategyBundle(packages=indexed)


def load_handoff(directory: Path | str) -> StrategyBundle:
    """Load every package in a directory and resolve the handoff as a whole."""
    return resolve_handoff(load_strategy_packages(directory))


def describe_handoff(bundle: StrategyBundle) -> dict[str, Any]:
    """What HELIOS accepted, in the terms HSA authored it.

    Returned to HSA as confirmation: which identities HELIOS holds, which
    semantic roles each package bound to which timeframe, and which components
    each chain resolved to. Nothing here is evaluation output.
    """
    return {
        "package_schema_version": next(
            (package.schema_version for package in bundle.packages.values()), None
        ),
        "atomic_strategies": [
            {
                "identity": package_identity(package),
                "role_timeframes": {
                    role: timeframe.code
                    for role, timeframe in sorted(package.role_timeframes.items())
                },
                "required_fields": sorted(
                    {name for item in package.inputs for name in item.required_fields}
                ),
            }
            for package in bundle.atomics
        ],
        "chains": [
            {
                "identity": package_identity(package),
                "primitive": package.chain.primitive.value if package.chain else None,
                "components": [
                    f"{component.strategy_id}@{component.strategy_version}"
                    for component in (package.chain.components if package.chain else ())
                ],
                "role_timeframes": {
                    role: timeframe.code
                    for role, timeframe in sorted(package.role_timeframes.items())
                },
            }
            for package in bundle.chains
        ],
    }
