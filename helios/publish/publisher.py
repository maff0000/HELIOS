"""The FALCON publication adapter.

One object owns the act of publishing: it canonicalises the envelope, asserts
the schema-version contract at the boundary, hands the bytes to the configured
sink and records that it did so. Strategies and chains do not serialise their
own output and do not know what a sink is.

Determinism is the point. ``StatePublisher`` adds nothing to the payload — no
publication timestamp, no sequence number, no host identity. Two HELIOS
processes given identical state publish identical bytes, which is what makes
the golden contract fixtures in ``fixtures/falcon/`` meaningful and what lets
FALCON diff two runs.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from helios.contracts.output import ENVELOPE_SCHEMA_VERSION, StrategyStateEnvelope
from helios.errors import ContractViolationError
from helios.observability.logging import get_logger
from helios.publish.sink import PublicationSink


class StatePublisher:
    """Publishes normalised strategy state to one configured sink."""

    __slots__ = ("_sink", "_logger", "_published_count")

    def __init__(
        self, sink: PublicationSink, *, logger: Optional[logging.Logger] = None
    ) -> None:
        if not isinstance(sink, PublicationSink):
            raise ContractViolationError(
                "a publication sink must implement emit/flush/close",
                received_type=type(sink).__name__,
            )
        self._sink = sink
        self._logger = logger if logger is not None else get_logger("publish")
        self._published_count = 0

    @property
    def sink(self) -> PublicationSink:
        return self._sink

    @property
    def published_count(self) -> int:
        """How many envelopes this publisher has emitted."""
        return self._published_count

    def render(self, envelope: StrategyStateEnvelope) -> str:
        """The exact bytes this envelope publishes as, without emitting them.

        Separated from :meth:`publish` so that a contract fixture, a test or a
        consumer-side comparison uses the identical code path the live
        publication uses.
        """
        if not isinstance(envelope, StrategyStateEnvelope):
            raise ContractViolationError(
                "only a StrategyStateEnvelope may be published; HELIOS does not "
                "publish strategy-specific formats",
                received_type=type(envelope).__name__,
            )
        # The model already refuses a foreign schema_version on construction.
        # Re-asserting it here states the contract at the boundary where it is
        # promised, so a future change to the model cannot silently publish an
        # envelope FALCON was never told how to read.
        if envelope.schema_version != ENVELOPE_SCHEMA_VERSION:
            raise ContractViolationError(
                "refusing to publish an envelope whose schema_version is not the "
                "one this build promises",
                received=envelope.schema_version,
                published=ENVELOPE_SCHEMA_VERSION,
            )
        return envelope.to_canonical_json()

    def publish(self, envelope: StrategyStateEnvelope) -> str:
        """Publish one envelope; returns the exact payload emitted."""
        payload = self.render(envelope)
        self._sink.emit(payload)
        self._published_count += 1
        self._logger.debug(
            "published strategy state",
            extra={
                "envelope_schema_version": envelope.schema_version,
                "publication_sink": self._sink.description,
                "strategy_id": str(envelope.strategy_id),
                "strategy_version": str(envelope.strategy_version),
                "strategy_kind": envelope.kind.value,
                "strategy_state": envelope.state.value,
                "instrument": str(envelope.instrument),
                "last_evaluated_at_utc": envelope.last_evaluated_at_utc,
            },
        )
        return payload

    def publish_all(self, envelopes: Iterable[StrategyStateEnvelope]) -> tuple[str, ...]:
        """Publish many envelopes in the order given, returning their payloads."""
        return tuple(self.publish(envelope) for envelope in envelopes)

    def flush(self) -> None:
        self._sink.flush()

    def close(self) -> None:
        """Flush and release the sink. Safe to call more than once."""
        self._sink.close()

    def __enter__(self) -> "StatePublisher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
