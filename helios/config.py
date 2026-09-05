"""External runtime configuration. The single entry point.

There are no configuration values in HELIOS source. Every operational number —
freshness multipliers, grace periods, accepted upstream schema versions, log
level, and where strategy state is published — is supplied from the
environment or from a TOML file named by ``HELIOS_CONFIG_FILE``, and validated
at startup.

:func:`load_config` is the ONLY configuration entry point. Publication settings
are loaded here alongside the rest rather than by a second loader of their own:
one entry point means one precedence rule, one file read, and — because every
problem lands in one list — one error telling an operator everything that is
wrong with a deployment.

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
from helios.publish.config import (
    SINK_FILE,
    SINK_MEMORY,
    SINK_STREAM,
    SUPPORTED_SINKS,
    SUPPORTED_STREAMS,
    PublicationConfig,
)
from helios.runtime.config import (
    REQUIRED_FOR_RUNTIME,
    SUPPORTED_FEED_END_ACTIONS,
    RuntimeConfig,
)

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
    ("publication_sink", "HELIOS_PUBLICATION_SINK", ("publication", "sink")),
)

#: Optional settings, same shape.
OPTIONAL_SETTINGS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "freshness_overrides",
        "HELIOS_FRESHNESS_OVERRIDES",
        ("freshness", "overrides"),
    ),
    ("strategy_package_dir", "HELIOS_STRATEGY_PACKAGE_DIR", ("strategies", "package_dir")),
    ("publication_path", "HELIOS_PUBLICATION_PATH", ("publication", "path")),
    ("publication_stream", "HELIOS_PUBLICATION_STREAM", ("publication", "stream")),
    # The running service's own settings. Optional HERE, and required by
    # HeliosConfig.runtime(): a contract test or a one-off script is a valid
    # HELIOS process with no feed and no status file, and demanding these of it
    # would be configuration theatre. See helios/runtime/config.py.
    ("runtime_instrument", "HELIOS_RUNTIME_INSTRUMENT", ("runtime", "instrument")),
    ("runtime_feed_dir", "HELIOS_RUNTIME_FEED_DIR", ("runtime", "feed_dir")),
    ("runtime_tick_seconds", "HELIOS_RUNTIME_TICK_SECONDS", ("runtime", "tick_seconds")),
    ("runtime_on_feed_end", "HELIOS_RUNTIME_ON_FEED_END", ("runtime", "on_feed_end")),
    ("runtime_status_file", "HELIOS_RUNTIME_STATUS_FILE", ("runtime", "status_file")),
    (
        "runtime_health_max_age_seconds",
        "HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS",
        ("runtime", "health_max_age_seconds"),
    ),
    ("runtime_max_cycles", "HELIOS_RUNTIME_MAX_CYCLES", ("runtime", "max_cycles")),
)

#: Precedence of the layer a setting came from. The environment wins over the
#: file, so one container image runs in every environment and only the injected
#: environment differs.
FROM_NOWHERE = 0
FROM_FILE = 1
FROM_ENVIRONMENT = 2


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
    publication_sink: str
    publication_path: Optional[Path]
    publication_stream: Optional[str]
    runtime_instrument: Optional[str]
    runtime_feed_dir: Optional[Path]
    runtime_tick_seconds: Optional[int]
    runtime_on_feed_end: Optional[str]
    runtime_status_file: Optional[Path]
    runtime_health_max_age_seconds: Optional[int]
    runtime_max_cycles: Optional[int]

    def runtime(self) -> RuntimeConfig:
        """The settings the running service needs, or a loud refusal.

        ``load_config`` has already checked the shape of everything supplied.
        What it cannot know is whether this process intends to *run*: a
        contract test is a perfectly valid HELIOS process with no feed and no
        status file. So presence is asserted here, at the moment the service
        starts, and every absent setting is reported at once — an operator
        fixes one deployment rather than discovering faults one restart at a
        time, which is the same promise ``load_config`` makes.
        """
        values = {
            "instrument": self.runtime_instrument,
            "feed_dir": self.runtime_feed_dir,
            "package_dir": self.strategy_package_dir,
            "tick_seconds": self.runtime_tick_seconds,
            "on_feed_end": self.runtime_on_feed_end,
            "status_file": self.runtime_status_file,
            "health_max_age_seconds": self.runtime_health_max_age_seconds,
        }
        missing = [
            f"{attribute}: required to run the HELIOS service "
            f"(set {env_var} or {toml_path} in the config file)"
            for attribute, env_var, toml_path in REQUIRED_FOR_RUNTIME
            if values[attribute] is None
        ]
        if missing:
            raise ConfigurationError(
                "incomplete HELIOS runtime configuration", problems=sorted(missing)
            )
        assert self.runtime_instrument is not None
        assert self.runtime_feed_dir is not None and self.strategy_package_dir is not None
        assert self.runtime_tick_seconds is not None and self.runtime_on_feed_end is not None
        assert self.runtime_status_file is not None
        assert self.runtime_health_max_age_seconds is not None
        return RuntimeConfig(
            instrument=self.runtime_instrument,
            feed_dir=self.runtime_feed_dir,
            package_dir=self.strategy_package_dir,
            tick_seconds=self.runtime_tick_seconds,
            on_feed_end=self.runtime_on_feed_end,
            status_file=self.runtime_status_file,
            health_max_age_seconds=self.runtime_health_max_age_seconds,
            max_cycles=self.runtime_max_cycles,
        )

    def publication(self) -> PublicationConfig:
        """The publication destination this deployment was configured with."""
        return PublicationConfig(
            sink=self.publication_sink,
            path=self.publication_path,
            stream=self.publication_stream,
        )

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


def _as_directory(value: Any, *, name: str, problems: list[str]) -> Optional[Path]:
    """A path that must already be a directory. HELIOS creates no input it was
    not configured for, and a mistyped path must fail at startup."""
    if value is None or not str(value).strip():
        return None
    path = Path(str(value).strip())
    if not path.is_dir():
        problems.append(f"{name}: not a directory ({path})")
        return None
    return path


def _resolve_publication(
    raw: Mapping[str, Any], source: Mapping[str, int], problems: list[str]
) -> tuple[Optional[str], Optional[Path], Optional[str]]:
    """Validate the publication settings, appending to the shared problem list.

    A setting belonging to a DIFFERENT sink is normally a contradiction, not a
    harmless extra: the operator believes they configured something HELIOS is
    not doing, and quietly ignoring it is how a deployment ends up publishing
    somewhere nobody is reading. The one exception is a setting a
    higher-precedence layer has overridden away — a file describing a FILE
    deployment, with the environment selecting MEMORY for a dry run, is an
    override, not a mistake.
    """
    sink: Optional[str] = None
    if raw["publication_sink"] is not None:
        sink = str(raw["publication_sink"]).strip().upper()
        if not sink:
            sink = None
        elif sink not in SUPPORTED_SINKS:
            problems.append(
                f"publication_sink: expected one of {list(SUPPORTED_SINKS)}, "
                f"received {sink!r}"
            )
            sink = None

    path: Optional[Path] = None
    if raw["publication_path"] is not None and str(raw["publication_path"]).strip():
        path = Path(str(raw["publication_path"]).strip())

    stream: Optional[str] = None
    if raw["publication_stream"] is not None and str(raw["publication_stream"]).strip():
        stream = str(raw["publication_stream"]).strip().upper()
        if stream not in SUPPORTED_STREAMS:
            problems.append(
                f"publication_stream: expected one of {list(SUPPORTED_STREAMS)}, "
                f"received {stream!r}"
            )
            stream = None

    def stray(attribute: str, message: str) -> None:
        if source.get(attribute, FROM_NOWHERE) >= source.get(
            "publication_sink", FROM_NOWHERE
        ):
            problems.append(message)

    if sink == SINK_FILE:
        if path is None:
            problems.append(
                "publication_path: sink FILE requires a destination "
                "(set HELIOS_PUBLICATION_PATH or [publication] path)"
            )
        if stream is not None:
            stray("publication_stream", "publication_stream: meaningful only for sink STREAM")
    elif sink == SINK_STREAM:
        if stream is None:
            problems.append(
                "publication_stream: sink STREAM requires a destination "
                f"(set HELIOS_PUBLICATION_STREAM to one of {list(SUPPORTED_STREAMS)})"
            )
        if path is not None:
            stray("publication_path", "publication_path: meaningful only for sink FILE")
    elif sink == SINK_MEMORY:
        if path is not None:
            stray("publication_path", "publication_path: meaningful only for sink FILE")
        if stream is not None:
            stray("publication_stream", "publication_stream: meaningful only for sink STREAM")

    # Only the settings this sink actually uses are carried forward, so a
    # loaded configuration never describes a destination HELIOS is not using.
    return sink, (path if sink == SINK_FILE else None), (stream if sink == SINK_STREAM else None)


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

    def resolve(env_var: str, toml_path: tuple[str, ...]) -> tuple[Any, int]:
        # Environment wins over file: the container image is identical across
        # environments and only the injected environment differs. The layer a
        # value came from is reported too, because one rule needs it: a setting
        # a higher-precedence layer overrode away is an override, not a mistake.
        if env_var in environ and str(environ[env_var]).strip() != "":
            return environ[env_var], FROM_ENVIRONMENT
        value = _lookup_toml(document, toml_path)
        return value, (FROM_FILE if value is not None else FROM_NOWHERE)

    raw: dict[str, Any] = {}
    source: dict[str, int] = {}
    for attribute, env_var, toml_path in REQUIRED_SETTINGS:
        value, layer = resolve(env_var, toml_path)
        if value is None or not str(value).strip():
            problems.append(
                f"{attribute}: required configuration is missing "
                f"(set {env_var} or [{'.'.join(toml_path[:-1]) or 'root'}] "
                f"{toml_path[-1]} in the config file)"
            )
        raw[attribute], source[attribute] = value, layer
    for attribute, env_var, toml_path in OPTIONAL_SETTINGS:
        raw[attribute], source[attribute] = resolve(env_var, toml_path)

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

    publication_sink, publication_path, publication_stream = _resolve_publication(
        raw, source, problems
    )

    package_dir = _as_directory(
        raw["strategy_package_dir"], name="strategy_package_dir", problems=problems
    )
    runtime_feed_dir = _as_directory(
        raw["runtime_feed_dir"], name="runtime_feed_dir", problems=problems
    )

    runtime_instrument: Optional[str] = None
    if raw["runtime_instrument"] is not None and str(raw["runtime_instrument"]).strip():
        runtime_instrument = str(raw["runtime_instrument"]).strip()

    runtime_tick_seconds = (
        _as_int(
            raw["runtime_tick_seconds"],
            name="runtime_tick_seconds",
            problems=problems,
            minimum=0,
        )
        if raw["runtime_tick_seconds"] is not None
        else None
    )
    runtime_max_cycles = (
        _as_int(
            raw["runtime_max_cycles"], name="runtime_max_cycles", problems=problems, minimum=1
        )
        if raw["runtime_max_cycles"] is not None
        else None
    )
    runtime_health_max_age_seconds = (
        _as_int(
            raw["runtime_health_max_age_seconds"],
            name="runtime_health_max_age_seconds",
            problems=problems,
            minimum=1,
        )
        if raw["runtime_health_max_age_seconds"] is not None
        else None
    )

    runtime_on_feed_end: Optional[str] = None
    if raw["runtime_on_feed_end"] is not None and str(raw["runtime_on_feed_end"]).strip():
        runtime_on_feed_end = str(raw["runtime_on_feed_end"]).strip().upper()
        if runtime_on_feed_end not in SUPPORTED_FEED_END_ACTIONS:
            problems.append(
                f"runtime_on_feed_end: expected one of "
                f"{list(SUPPORTED_FEED_END_ACTIONS)}, received {runtime_on_feed_end!r}"
            )
            runtime_on_feed_end = None

    # The status file itself need not exist yet — the service creates it — but
    # the directory it lives in must, for the same reason the FILE sink refuses
    # to invent a destination: a mistyped path must fail at startup rather than
    # leave a health probe reading a file nobody writes.
    runtime_status_file: Optional[Path] = None
    if raw["runtime_status_file"] is not None and str(raw["runtime_status_file"]).strip():
        runtime_status_file = Path(str(raw["runtime_status_file"]).strip())
        parent = runtime_status_file.parent if str(runtime_status_file.parent) else Path(".")
        if not parent.is_dir():
            problems.append(
                f"runtime_status_file: directory does not exist ({parent})"
            )

    if problems:
        raise ConfigurationError(
            "invalid HELIOS configuration", problems=sorted(problems)
        )

    assert environment and log_level and multiplier and accepted is not None
    assert grace is not None and allow_incomplete is not None
    assert publication_sink is not None
    return HeliosConfig(
        runtime_instrument=runtime_instrument,
        runtime_feed_dir=runtime_feed_dir,
        runtime_tick_seconds=runtime_tick_seconds,
        runtime_on_feed_end=runtime_on_feed_end,
        runtime_status_file=runtime_status_file,
        runtime_health_max_age_seconds=runtime_health_max_age_seconds,
        runtime_max_cycles=runtime_max_cycles,
        environment=environment,
        log_level=log_level,
        freshness_max_age_multiplier=multiplier,
        freshness_grace_seconds=grace,
        freshness_allow_incomplete_frames=allow_incomplete,
        accepted_hermes_schema_versions=accepted,
        freshness_overrides=overrides,
        strategy_package_dir=package_dir,
        publication_sink=publication_sink,
        publication_path=publication_path,
        publication_stream=publication_stream,
    )
