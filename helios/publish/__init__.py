"""The FALCON publication boundary.

HELIOS publishes normalised strategy state as canonical JSON lines to a sink
chosen by external configuration. This package owns:

* :mod:`helios.publish.sink` — where payloads go (file, stream, memory). There
  is no network sink and no state service; the PID forbids a public internal
  state service by default.
* :mod:`helios.publish.publisher` — the adapter that serialises an envelope
  once, deterministically, and hands the bytes to the sink.
* :mod:`helios.publish.negotiation` — the schema-version rule a consumer must
  implement: refuse an unrecognised ``schema_version`` rather than interpret it.
* :mod:`helios.publish.config` — what the destination can be, and how a
  validated choice becomes a sink. Nothing is defaulted in source, and nothing
  is loaded here: :func:`helios.config.load_config` is the single
  configuration entry point.
* :mod:`helios.publish.schema` — the published JSON Schema FALCON validates
  against, generated from the model and checked in under ``docs/schema/``.
"""

from helios.publish.config import (
    SINK_FILE,
    SINK_MEMORY,
    SINK_STREAM,
    SUPPORTED_SINKS,
    SUPPORTED_STREAMS,
    PublicationConfig,
    build_sink,
)
from helios.publish.negotiation import (
    ENVELOPE_SCHEMA_NAMESPACE,
    SCHEMA_VERSION_FIELD,
    SUPPORTED_ENVELOPE_SCHEMA_VERSIONS,
    accept_payload,
    negotiate_schema_version,
    read_schema_version,
    schema_namespace,
)
from helios.publish.publisher import StatePublisher
from helios.publish.schema import (
    CANONICAL_DECIMAL_PATTERN,
    CANONICAL_INSTANT_PATTERN,
    SCHEMA_PATH,
    published_schema,
    render_schema,
    write_schema,
)
from helios.publish.sink import (
    RECORD_TERMINATOR,
    FileSink,
    InMemorySink,
    PublicationSink,
    StreamSink,
)

__all__ = [
    "CANONICAL_DECIMAL_PATTERN",
    "CANONICAL_INSTANT_PATTERN",
    "ENVELOPE_SCHEMA_NAMESPACE",
    "RECORD_TERMINATOR",
    "SCHEMA_PATH",
    "SCHEMA_VERSION_FIELD",
    "SINK_FILE",
    "SINK_MEMORY",
    "SINK_STREAM",
    "SUPPORTED_ENVELOPE_SCHEMA_VERSIONS",
    "SUPPORTED_SINKS",
    "SUPPORTED_STREAMS",
    "FileSink",
    "InMemorySink",
    "PublicationConfig",
    "PublicationSink",
    "StatePublisher",
    "StreamSink",
    "accept_payload",
    "build_sink",
    "negotiate_schema_version",
    "published_schema",
    "read_schema_version",
    "render_schema",
    "schema_namespace",
    "write_schema",
]
