"""External runtime configuration.

There are no configuration values in HELIOS source. Every operational number —
freshness multipliers, grace periods, accepted upstream schema versions, log
level — is supplied from the environment or from a TOML file named by
``HELIOS_CONFIG_FILE``, and validated at startup.

Missing or invalid required configuration is fatal and loud: :func:`load_config`
reports *every* problem it found at once, so an operator fixes one deployment
rather than discovering faults one restart at a time.

Nothing here reads a secret. HELIOS needs no credential to evaluate strategies
against market facts; if a later work item needs one, it belongs in the
environment and must never be logged or echoed.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

from helios.contracts.freshness import FreshnessPolicy
from helios.contracts.timeframe import Timeframe
from helios.errors import ConfigurationError

#: Environment variable naming the optional TOML configuration file.
CONFIG_FILE_ENV_VAR = "HELIOS_CONFIG_FILE"

#: Prefix for every HELIOS environment variable.
ENV_PREFIX = "HELIOS_"

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

#: Every required setting, as (attribute, environment variable, TOML path).
REQUIRED_SETTINGS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("environment", "HELIOS_ENVIRONMENT", ("environment",)),
    ("log_level", "HELIOS_LOG_LEVEL", ("logging", "level")),
    (
        "freshness_max_age_multiplier",
        "HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER",
        ("freshness", "max_age_multiplier"),
    ),
    (
        "freshness_grace_seconds",
        "HELIOS_FRESHNESS_GRACE_SECONDS",
        ("freshness", "grace_seconds"),
    ),
    (
        "freshness_allow_incomplete_frames",
        "HELIOS_FRESHNESS_ALLOW_INCOMPLETE_FRAMES",
        ("freshness", "allow_incomplete_frames"),
    ),
    (
        "accepted_hermes_schema_versions",
        "HELIOS_ACCEPTED_HERMES_SCHEMA_VERSIONS",
        ("hermes", "accepted_schema_versions"),
    ),
)

#: Optional settings, same shape.
OPTIONAL_SETTINGS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "freshness_overrides",
        "HELIOS_FRESHNESS_OVERRIDES",
        ("freshness", "overrides"),
    ),
    ("strategy_package_dir", "HELIOS_STRATEGY_PACKAGE_DIR", ("strategies", "package_dir")),
)


@dataclass(frozen=True, slots=True)
class HeliosConfig:
    """Validated runtime configuration. Immutable once loaded."""

    environment: str
    log_level: str
    freshness_max_age_multiplier: str
    freshness_grace_seconds: int
    freshness_allow_incomplete_frames: bool
    accepted_hermes_schema_versions: tuple[str, ...]
    freshness_overrides: Mapping[Timeframe, timedelta]
    strategy_package_dir: Optional[Path]

    def freshness_policy(self) -> FreshnessPolicy:
        """Build the freshness policy this deployment was configured with."""
        return FreshnessPolicy(
            max_age_multiplier=self.freshness_max_age_multiplier,
            grace=timedelta(seconds=self.freshness_grace_seconds),
            allow_incomplete_frames=self.freshness_allow_incomplete_frames,
            overrides=self.freshness_overrides,
        )

    def accepts_schema_version(self, schema_version: str) -> bool:
        """Whether an incoming HERMES fact schema version is one we understand."""
        return schema_version in self.accepted_hermes_schema_versions


def _lookup_toml(document: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = document
    for part in path:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _as_bool(value: Any, *, name: str, problems: list[str]) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    problems.append(f"{name}: expected a boolean, received {value!r}")
    return None


def _as_int(value: Any, *, name: str, problems: list[str], minimum: int) -> Optional[int]:
    if isinstance(value, bool):
        problems.append(f"{name}: expected an integer, received a boolean")
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            problems.append(f"{name}: expected an integer, received {value!r}")
            return None
    else:
        problems.append(f"{name}: expected an integer, received {type(value).__name__}")
        return None
    if parsed < minimum:
        problems.append(f"{name}: must be >= {minimum}, received {parsed}")
        return None
    return parsed


def _as_decimal_text(value: Any, *, name: str, problems: list[str]) -> Optional[str]:
    text = str(value).strip()
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        problems.append(f"{name}: expected a decimal number, received {value!r}")
        return None
    if not parsed.is_finite() or parsed <= 0:
        problems.append(f"{name}: must be a positive finite number, received {value!r}")
        return None
    return text


def _as_string_tuple(value: Any, *, name: str, problems: list[str]) -> Optional[tuple[str, ...]]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value if str(part).strip()]
    else:
        problems.append(f"{name}: expected a list or comma-separated string")
        return None
    if not items:
        problems.append(f"{name}: must list at least one value")
        return None
    return tuple(items)


def _as_overrides(
    value: Any, *, name: str, problems: list[str]
) -> Mapping[Timeframe, timedelta]:
    if value is None:
        return MappingProxyType({})
    pairs: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        pairs = [(str(key), item) for key, item in value.items()]
    elif isinstance(value, str):
        for chunk in value.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                problems.append(f"{name}: expected 'TIMEFRAME=seconds' entries, got {chunk!r}")
                continue
            key, _, item = chunk.partition("=")
            pairs.append((key.strip(), item.strip()))
    else:
        problems.append(f"{name}: expected a table or 'TIMEFRAME=seconds' string")
        return MappingProxyType({})
    result: dict[Timeframe, timedelta] = {}
    for key, item in pairs:
        try:
            timeframe = Timeframe.parse(key)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            problems.append(f"{name}: {exc}")
            continue
        seconds = _as_int(item, name=f"{name}[{key}]", problems=problems, minimum=1)
        if seconds is not None:
            result[timeframe] = timedelta(seconds=seconds)
    return MappingProxyType(result)


def load_config(
    environ: Optional[Mapping[str, str]] = None,
    *,
    config_file: Optional[Path | str] = None,
) -> HeliosConfig:
    """Load and validate configuration, failing loudly with every problem found."""
    environ = os.environ if environ is None else environ
    problems: list[str] = []

    document: Mapping[str, Any] = {}
    file_path = config_file if config_file is not None else environ.get(CONFIG_FILE_ENV_VAR)
    if file_path:
        path = Path(file_path)
        if not path.is_file():
            raise ConfigurationError(
                "configuration file named but not found", config_file=str(path)
            )
        try:
            with path.open("rb") as handle:
                document = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(
                "malformed configuration file", config_file=str(path), detail=str(exc)
            ) from exc

    def resolve(env_var: str, toml_path: tuple[str, ...]) -> Any:
        # Environment wins over file: the container image is identical across
        # environments and only the injected environment differs.
        if env_var in environ and str(environ[env_var]).strip() != "":
            return environ[env_var]
        return _lookup_toml(document, toml_path)

    raw: dict[str, Any] = {}
    for attribute, env_var, toml_path in REQUIRED_SETTINGS:
        value = resolve(env_var, toml_path)
        if value is None:
            problems.append(
                f"{attribute}: required configuration is missing "
                f"(set {env_var} or [{'.'.join(toml_path[:-1]) or 'root'}] "
                f"{toml_path[-1]} in the config file)"
            )
        raw[attribute] = value
    for attribute, env_var, toml_path in OPTIONAL_SETTINGS:
        raw[attribute] = resolve(env_var, toml_path)

    environment = None
    if raw["environment"] is not None:
        environment = str(raw["environment"]).strip()
        if not environment:
            problems.append("environment: must not be empty")

    log_level = None
    if raw["log_level"] is not None:
        log_level = str(raw["log_level"]).strip().upper()
        if log_level not in _VALID_LOG_LEVELS:
            problems.append(
                f"log_level: expected one of {list(_VALID_LOG_LEVELS)}, received {log_level!r}"
            )

    multiplier = (
        _as_decimal_text(
            raw["freshness_max_age_multiplier"],
            name="freshness_max_age_multiplier",
            problems=problems,
        )
        if raw["freshness_max_age_multiplier"] is not None
        else None
    )
    grace = (
        _as_int(
            raw["freshness_grace_seconds"],
            name="freshness_grace_seconds",
            problems=problems,
            minimum=0,
        )
        if raw["freshness_grace_seconds"] is not None
        else None
    )
    allow_incomplete = (
        _as_bool(
            raw["freshness_allow_incomplete_frames"],
            name="freshness_allow_incomplete_frames",
            problems=problems,
        )
        if raw["freshness_allow_incomplete_frames"] is not None
        else None
    )
    accepted = (
        _as_string_tuple(
            raw["accepted_hermes_schema_versions"],
            name="accepted_hermes_schema_versions",
            problems=problems,
        )
        if raw["accepted_hermes_schema_versions"] is not None
        else None
    )
    overrides = _as_overrides(
        raw["freshness_overrides"], name="freshness_overrides", problems=problems
    )

    package_dir: Optional[Path] = None
    if raw["strategy_package_dir"] is not None:
        package_dir = Path(str(raw["strategy_package_dir"]))
        if not package_dir.is_dir():
            problems.append(
                f"strategy_package_dir: not a directory ({package_dir})"
            )

    if problems:
        raise ConfigurationError(
            "invalid HELIOS configuration", problems=sorted(problems)
        )

    assert environment and log_level and multiplier and accepted is not None
    assert grace is not None and allow_incomplete is not None
    return HeliosConfig(
        environment=environment,
        log_level=log_level,
        freshness_max_age_multiplier=multiplier,
        freshness_grace_seconds=grace,
        freshness_allow_incomplete_frames=allow_incomplete,
        accepted_hermes_schema_versions=accepted,
        freshness_overrides=overrides,
        strategy_package_dir=package_dir,
    )
