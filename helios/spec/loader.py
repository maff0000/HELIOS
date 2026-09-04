"""Loading HSA strategy packages from JSON or YAML.

Numeric scalars are read as exact decimals, never as binary floats, so a
package's parameters mean exactly what HSA wrote. Every failure — unreadable
file, unknown format, malformed structure, ambiguous definition — raises
:class:`~helios.errors.StrategySpecError` and names the file.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from pydantic import ValidationError

from helios.contracts._fields import summarise_validation_error
from helios.errors import ContractViolationError, HeliosError, StrategySpecError
from helios.spec.model import StrategyPackage

#: File suffixes a strategy package may use.
SUPPORTED_SUFFIXES: tuple[str, ...] = (".json", ".yaml", ".yml")


class _DecimalSafeLoader(yaml.SafeLoader):
    """A YAML loader that reads numeric scalars as exact decimals."""


def _construct_decimal(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    text = str(loader.construct_scalar(node))  # type: ignore[arg-type]
    try:
        return Decimal(text)
    except InvalidOperation:
        # Leave .inf/.nan and friends to fail loudly in model validation.
        return text


_DecimalSafeLoader.add_constructor("tag:yaml.org,2002:float", _construct_decimal)


def parse_strategy_package(data: Any, *, origin: str) -> StrategyPackage:
    """Validate already-parsed data into a :class:`StrategyPackage`."""
    if not isinstance(data, Mapping):
        raise StrategySpecError(
            "a strategy package must be a mapping at its top level",
            origin=origin,
            received_type=type(data).__name__,
        )
    try:
        return StrategyPackage(**dict(data))
    except StrategySpecError as exc:
        raise StrategySpecError(exc.message, origin=origin, **dict(exc.context)) from exc
    except ContractViolationError as exc:
        raise StrategySpecError(
            "malformed strategy package", origin=origin, **dict(exc.context)
        ) from exc
    except ValidationError as exc:  # pragma: no cover - HeliosModel wraps these
        raise StrategySpecError(
            "malformed strategy package",
            origin=origin,
            problems=summarise_validation_error(exc),
        ) from exc
    except HeliosError as exc:
        raise StrategySpecError(exc.message, origin=origin, **dict(exc.context)) from exc


def load_strategy_package(path: Path | str) -> StrategyPackage:
    """Read one strategy package from disk."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise StrategySpecError(
            "unsupported strategy package format",
            origin=str(path),
            suffix=path.suffix,
            supported=list(SUPPORTED_SUFFIXES),
        )
    if not path.is_file():
        raise StrategySpecError("strategy package not found", origin=str(path))
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise StrategySpecError(
                "malformed JSON in strategy package", origin=str(path), detail=str(exc)
            ) from exc
    else:
        try:
            data = yaml.load(text, Loader=_DecimalSafeLoader)
        except yaml.YAMLError as exc:
            raise StrategySpecError(
                "malformed YAML in strategy package", origin=str(path), detail=str(exc)
            ) from exc
    return parse_strategy_package(data, origin=str(path))


def load_strategy_packages(directory: Path | str) -> dict[str, StrategyPackage]:
    """Load every package in a directory, keyed by ``strategy_id@version``.

    A duplicated identity is a loud failure: two definitions claiming one
    promoted version would make published state non-reproducible.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise StrategySpecError("strategy package directory not found", origin=str(directory))
    packages: dict[str, StrategyPackage] = {}
    origins: dict[str, str] = {}
    for path in _package_paths(directory):
        package = load_strategy_package(path)
        key = f"{package.identity.strategy_id}@{package.identity.strategy_version}"
        if key in packages:
            raise StrategySpecError(
                "duplicate strategy identity across packages",
                identity=key,
                origin=str(path),
                previous_origin=origins[key],
            )
        packages[key] = package
        origins[key] = str(path)
    return packages


def _package_paths(directory: Path) -> Iterable[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )
