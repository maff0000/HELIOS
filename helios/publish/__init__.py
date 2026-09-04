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
* :mod:`helios.publish.config` — the external configuration that decides the
  destination. Nothing is defaulted in source.
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
    load_publication_config,
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
    "load_publication_config",
    "negotiate_schema_version",
    "published_schema",
    "read_schema_version",
    "render_schema",
    "schema_namespace",
    "write_schema",
]
