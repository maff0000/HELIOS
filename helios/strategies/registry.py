"""Binding a validated strategy package to a concrete atomic implementation.

A strategy package is a declaration; an atom module is the code that can
evaluate it. This registry is the only place the two meet, and it is
deliberately unforgiving:

* the binding key is the package's own ``strategy_id`` — the package format
  has no separate "implementation" field, and inventing one would give HSA two
  ways to name the same thing;
* a package naming an atom nobody registered is refused, not skipped;
* a parameter the atom does not use is refused, so a typo cannot become a
  silent no-op;
* a parameter the atom needs and the package does not declare is refused —
  HELIOS supplies no default, because a default is invented trading logic;
* a value outside either the atom's limits or the package's own declared range
  is refused.

There is deliberately no facility for overriding a package's parameters at
bind time. A promoted ``(strategy_id, strategy_version)`` is immutable and is
the key CER records evidence against; a different parameter value is a
different definition and therefore a new version, not a runtime argument.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from helios.contracts._fields import to_decimal
from helios.errors import StrategySpecError
from helios.spec.model import PackageKind, ParameterSpec, ParameterType, StrategyPackage
from helios.strategies.base import AtomicStrategy, ParameterRequirement


class AtomRegistry:
    """The atomic strategies this deployment can evaluate."""

    __slots__ = ("_atoms",)

    def __init__(self) -> None:
        self._atoms: dict[str, type[AtomicStrategy]] = {}

    def register(self, atom_type: type[AtomicStrategy]) -> type[AtomicStrategy]:
        """Make one atomic strategy implementation available for binding."""
        if not isinstance(atom_type, type) or not issubclass(atom_type, AtomicStrategy):
            raise StrategySpecError(
                "only an AtomicStrategy subclass can be registered",
                received=getattr(atom_type, "__name__", repr(atom_type)),
            )
        name = atom_type.ATOM_NAME
        if not name:
            raise StrategySpecError(
                "an atomic strategy must declare the ATOM_NAME its packages carry",
                implementation=atom_type.__name__,
            )
        existing = self._atoms.get(name)
        if existing is not None and existing is not atom_type:
            raise StrategySpecError(
                "two implementations claim the same atomic strategy name; published "
                "state would not be reproducible",
                atom=name,
                registered=existing.__name__,
                proposed=atom_type.__name__,
            )
        self._atoms[name] = atom_type
        return atom_type

    @property
    def registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._atoms))

    def implementation_for(self, name: str) -> type[AtomicStrategy]:
        atom_type = self._atoms.get(name)
        if atom_type is None:
            raise StrategySpecError(
                "no atomic strategy implementation is registered under this name; "
                "HELIOS will not guess which condition the package meant",
                atom=name,
                registered=list(self.registered),
            )
        return atom_type

    def build(self, package: StrategyPackage) -> AtomicStrategy:
        """Bind one validated ATOMIC package to its implementation."""
        if package.kind is not PackageKind.ATOMIC:
            raise StrategySpecError(
                "the atomic strategy registry binds ATOMIC packages only; a CHAIN "
                "package belongs to the composition layer",
                strategy_id=str(package.identity.strategy_id),
                kind=package.kind.value,
            )
        name = str(package.identity.strategy_id)
        atom_type = self.implementation_for(name)
        self._check_inputs(atom_type, package)
        parameters = self._resolve_parameters(atom_type, package)
        atom_type.validate_binding(package, parameters)
        return atom_type(package=package, parameters=parameters)

    def build_all(self, packages: Iterable[StrategyPackage]) -> tuple[AtomicStrategy, ...]:
        """Bind many packages, in a deterministic order.

        Ordered by canonical identity rather than by iteration order, so that a
        caller cannot make published output depend on how it happened to
        enumerate a directory.
        """
        ordered = sorted(
            packages, key=lambda item: f"{item.identity.strategy_id}@{item.identity.strategy_version}"
        )
        return tuple(self.build(package) for package in ordered)

    # ------------------------------------------------------------- internals

    @staticmethod
    def _check_inputs(atom_type: type[AtomicStrategy], package: StrategyPackage) -> None:
        if len(package.inputs) != 1:
            raise StrategySpecError(
                "an atomic strategy package declares exactly one semantic input; "
                "combining several roles is the composition layer's responsibility",
                strategy_id=str(package.identity.strategy_id),
                declared_inputs=len(package.inputs),
            )
        requirement = package.inputs[0]
        missing = sorted(
            set(atom_type.REQUIRED_FIELDS) - set(requirement.required_fields)
        )
        if missing:
            raise StrategySpecError(
                "the package's input does not declare every market fact this atomic "
                "strategy reads",
                strategy_id=str(package.identity.strategy_id),
                atom=atom_type.ATOM_NAME,
                missing=missing,
                declared=list(requirement.required_fields),
            )
        if requirement.lookback < atom_type.MIN_LOOKBACK:
            raise StrategySpecError(
                "the package declares less history than this atomic strategy needs",
                strategy_id=str(package.identity.strategy_id),
                atom=atom_type.ATOM_NAME,
                declared_lookback=requirement.lookback,
                minimum_lookback=atom_type.MIN_LOOKBACK,
            )

    @staticmethod
    def _resolve_parameters(
        atom_type: type[AtomicStrategy], package: StrategyPackage
    ) -> Mapping[str, Any]:
        required = {requirement.name: requirement for requirement in atom_type.PARAMETERS}
        declared = dict(package.parameters)
        unknown = sorted(set(declared) - set(required))
        if unknown:
            raise StrategySpecError(
                "the package declares parameters this atomic strategy does not use; "
                "a misspelt parameter must never become a silent no-op",
                strategy_id=str(package.identity.strategy_id),
                atom=atom_type.ATOM_NAME,
                unknown=unknown,
                accepted=sorted(required),
            )
        resolved: dict[str, Any] = {}
        for name in sorted(required):
            requirement = required[name]
            spec = declared.get(name)
            if spec is None:
                raise StrategySpecError(
                    "the package does not declare a parameter this atomic strategy "
                    "requires; HELIOS supplies no default for a strategy value",
                    strategy_id=str(package.identity.strategy_id),
                    atom=atom_type.ATOM_NAME,
                    parameter=name,
                )
            resolved[name] = _coerce_and_check(
                requirement, spec, strategy_id=str(package.identity.strategy_id)
            )
        return resolved


def _coerce_and_check(
    requirement: ParameterRequirement, spec: ParameterSpec, *, strategy_id: str
) -> Any:
    """Check one declared parameter against the atom's own contract."""
    if spec.type is not requirement.type:
        raise StrategySpecError(
            "the package declares this parameter with a different type than the "
            "atomic strategy reads",
            strategy_id=strategy_id,
            parameter=requirement.name,
            declared=spec.type.value,
            expected=requirement.type.value,
        )
    value = spec.value
    if requirement.type is ParameterType.BOOLEAN:
        if not isinstance(value, bool):
            raise _wrong_type(requirement, value, strategy_id)
        return value
    if requirement.type is ParameterType.STRING:
        if not isinstance(value, str):
            raise _wrong_type(requirement, value, strategy_id)
        return value
    if requirement.type is ParameterType.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _wrong_type(requirement, value, strategy_id)
        numeric = Decimal(value)
    else:
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise _wrong_type(requirement, value, strategy_id)
        numeric = to_decimal(value, field=requirement.name)
        value = numeric
    _check_range(requirement, numeric, spec, strategy_id)
    return value


def _wrong_type(
    requirement: ParameterRequirement, value: Any, strategy_id: str
) -> StrategySpecError:
    return StrategySpecError(
        "parameter value does not match the type the atomic strategy reads",
        strategy_id=strategy_id,
        parameter=requirement.name,
        expected=requirement.type.value,
        received_type=type(value).__name__,
    )


def _check_range(
    requirement: ParameterRequirement,
    numeric: Decimal,
    spec: ParameterSpec,
    strategy_id: str,
) -> None:
    """The tighter of the atom's limits and the package's own range wins."""
    minimum = _tighter(requirement.minimum, spec.minimum, keep_larger=True)
    maximum = _tighter(requirement.maximum, spec.maximum, keep_larger=False)
    if minimum is not None and numeric < minimum:
        raise StrategySpecError(
            "parameter value is below the minimum this atomic strategy accepts",
            strategy_id=strategy_id,
            parameter=requirement.name,
            value=str(numeric),
            minimum=str(minimum),
        )
    if maximum is not None and numeric > maximum:
        raise StrategySpecError(
            "parameter value is above the maximum this atomic strategy accepts",
            strategy_id=strategy_id,
            parameter=requirement.name,
            value=str(numeric),
            maximum=str(maximum),
        )


def _tighter(
    left: Optional[Decimal], right: Optional[Decimal], *, keep_larger: bool
) -> Optional[Decimal]:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right) if keep_larger else min(left, right)
