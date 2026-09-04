"""What the publication boundary can be configured to be.

There are no publication defaults in source. Which sink HELIOS publishes
through, and where it writes, are supplied externally and validated at startup
by :func:`helios.config.load_config` — the single configuration entry point,
and the single place that reports configuration problems. This module holds
what those settings MEAN: the sinks that exist, the validated shape, and how a
validated shape becomes a sink.

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

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from helios.errors import ConfigurationError
from helios.publish.sink import FileSink, InMemorySink, PublicationSink, StreamSink

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
