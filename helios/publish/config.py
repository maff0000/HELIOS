"""External configuration for the publication boundary.

There are no publication defaults in source. Which sink HELIOS publishes
through, and where it writes, come from the environment or from the same TOML
file :mod:`helios.config` reads (``HELIOS_CONFIG_FILE``), under a
``[publication]`` table. The environment wins over the file, so one container
image runs in every environment and only the injected environment differs.

Missing or invalid publication configuration is fatal and loud, and every
problem found is reported at once rather than one restart at a time. A setting
belonging to a sink other than the configured one is a problem too — quietly
ignoring it is how a deployment ends up publishing somewhere nobody reads —
unless a higher-precedence layer overrode it, which is an override rather than
a mistake.

Nothing here reads a secret. Publishing strategy state needs no credential:
the destination is a file or an already-open stream. There is no network sink
and no state service — see :mod:`helios.publish.sink`.

Settings
--------

===================================  ==========================  ==========================
environment variable                 TOML                        meaning
===================================  ==========================  ==========================
``HELIOS_PUBLICATION_SINK``          ``[publication] sink``      ``FILE``/``STREAM``/``MEMORY``
``HELIOS_PUBLICATION_PATH``          ``[publication] path``      file to append to (``FILE``)
``HELIOS_PUBLICATION_STREAM``        ``[publication] stream``    ``STDOUT``/``STDERR`` (``STREAM``)
===================================  ==========================  ==========================
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from helios.errors import ConfigurationError
from helios.publish.sink import FileSink, InMemorySink, PublicationSink, StreamSink

#: Environment variable naming the shared TOML configuration file.
CONFIG_FILE_ENV_VAR = "HELIOS_CONFIG_FILE"

#: TOML table these settings live in.
CONFIG_TABLE = "publication"

#: Append canonical JSON lines to a file.
SINK_FILE = "FILE"
#: Write canonical JSON lines to a standard stream.
SINK_STREAM = "STREAM"
#: Keep payloads in memory. Tests and dry runs; nothing durable is published.
SINK_MEMORY = "MEMORY"

#: Every sink an operator may configure. There is deliberately no network or
#: service sink: the PID forbids a public internal state service by default.
SUPPORTED_SINKS: tuple[str, ...] = (SINK_FILE, SINK_MEMORY, SINK_STREAM)

#: Streams ``SINK_STREAM`` may be pointed at.
SUPPORTED_STREAMS: tuple[str, ...] = ("STDERR", "STDOUT")

#: Precedence of the layer a setting came from. The environment wins.
_FROM_NOWHERE = 0
_FROM_FILE = 1
_FROM_ENVIRONMENT = 2

_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("sink", "HELIOS_PUBLICATION_SINK", "sink"),
    ("path", "HELIOS_PUBLICATION_PATH", "path"),
    ("stream", "HELIOS_PUBLICATION_STREAM", "stream"),
)


@dataclass(frozen=True, slots=True)
class PublicationConfig:
    """Validated publication configuration. Immutable once loaded."""

    sink: str
    path: Optional[Path] = None
    stream: Optional[str] = None

    @property
    def description(self) -> str:
        """A log-safe description of the configured destination."""
        if self.sink == SINK_FILE:
            return f"file:{self.path}"
        if self.sink == SINK_STREAM:
            return f"stream:{(self.stream or '').lower()}"
        return "memory"


def _read_document(
    environ: Mapping[str, str], config_file: Optional[Path | str]
) -> Mapping[str, Any]:
    file_path = config_file if config_file is not None else environ.get(CONFIG_FILE_ENV_VAR)
    if not file_path:
        return {}
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
    table = document.get(CONFIG_TABLE)
    return table if isinstance(table, Mapping) else {}


def load_publication_config(
    environ: Optional[Mapping[str, str]] = None,
    *,
    config_file: Optional[Path | str] = None,
) -> PublicationConfig:
    """Load and validate publication configuration, reporting every problem."""
    environ = os.environ if environ is None else environ
    table = _read_document(environ, config_file)
    problems: list[str] = []

    # Each setting is resolved independently, and the layer it came from is
    # remembered: the environment (rank 2) wins over the file (rank 1), which
    # is what lets one container image run everywhere.
    raw: dict[str, Any] = {}
    source: dict[str, int] = {}
    for attribute, env_var, key in _SETTINGS:
        value = environ.get(env_var)
        if value is not None and str(value).strip():
            raw[attribute], source[attribute] = value, _FROM_ENVIRONMENT
            continue
        raw[attribute] = table.get(key)
        source[attribute] = _FROM_FILE if raw[attribute] is not None else _FROM_NOWHERE

    sink = None
    if raw["sink"] is None or not str(raw["sink"]).strip():
        problems.append(
            "sink: required configuration is missing "
            "(set HELIOS_PUBLICATION_SINK or [publication] sink in the config file)"
        )
    else:
        sink = str(raw["sink"]).strip().upper()
        if sink not in SUPPORTED_SINKS:
            problems.append(
                f"sink: expected one of {list(SUPPORTED_SINKS)}, received {sink!r}"
            )
            sink = None

    path: Optional[Path] = None
    raw_path = raw["path"]
    if raw_path is not None and str(raw_path).strip():
        path = Path(str(raw_path).strip())
    stream: Optional[str] = None
    raw_stream = raw["stream"]
    if raw_stream is not None and str(raw_stream).strip():
        stream = str(raw_stream).strip().upper()
        if stream not in SUPPORTED_STREAMS:
            problems.append(
                f"stream: expected one of {list(SUPPORTED_STREAMS)}, received {stream!r}"
            )
            stream = None

    # Each sink needs exactly its own settings.
    #
    # A setting belonging to a DIFFERENT sink is normally a contradiction, not
    # a harmless extra: the operator believes they configured something HELIOS
    # is not doing, and quietly ignoring it is how a deployment ends up
    # publishing somewhere nobody is reading. The one exception is a setting a
    # higher-precedence layer has overridden away — a file describing a FILE
    # deployment, with the environment selecting MEMORY for a dry run, is an
    # override, not a mistake.
    def stray(attribute: str, message: str) -> None:
        if source.get(attribute, _FROM_NOWHERE) >= source.get("sink", _FROM_NOWHERE):
            problems.append(message)

    if sink == SINK_FILE:
        if path is None:
            problems.append(
                "path: sink FILE requires a destination "
                "(set HELIOS_PUBLICATION_PATH or [publication] path)"
            )
        if stream is not None:
            stray("stream", "stream: meaningful only for sink STREAM")
    elif sink == SINK_STREAM:
        if stream is None:
            problems.append(
                "stream: sink STREAM requires a destination "
                f"(set HELIOS_PUBLICATION_STREAM to one of {list(SUPPORTED_STREAMS)})"
            )
        if path is not None:
            stray("path", "path: meaningful only for sink FILE")
    elif sink == SINK_MEMORY:
        if path is not None:
            stray("path", "path: meaningful only for sink FILE")
        if stream is not None:
            stray("stream", "stream: meaningful only for sink STREAM")

    if problems:
        raise ConfigurationError(
            "invalid HELIOS publication configuration", problems=sorted(problems)
        )
    assert sink is not None
    # Only the settings this sink actually uses are carried forward, so a
    # PublicationConfig never describes a destination HELIOS is not using.
    return PublicationConfig(
        sink=sink,
        path=path if sink == SINK_FILE else None,
        stream=stream if sink == SINK_STREAM else None,
    )


def build_sink(config: PublicationConfig) -> PublicationSink:
    """Construct the configured sink. The choice is never made in source."""
    if not isinstance(config, PublicationConfig):
        raise ConfigurationError(
            "a validated PublicationConfig is required to build a sink",
            received_type=type(config).__name__,
        )
    if config.sink == SINK_FILE:
        assert config.path is not None
        return FileSink(config.path)
    if config.sink == SINK_STREAM:
        target = sys.stdout if config.stream == "STDOUT" else sys.stderr
        return StreamSink(target, label=(config.stream or "").lower())
    return InMemorySink()
