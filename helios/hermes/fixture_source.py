"""Reading canonical HERMES fixtures.

A fixture file is a self-describing, hand-checkable document of market facts
for one instrument and one timeframe, plus the reference instant those facts
should be judged fresh against. It is the offline stand-in for the live HERMES
feed and it goes through exactly the same contract validation, so a fixture
that HELIOS accepts is a fixture the real contract accepts.

Numbers are read with ``parse_float=Decimal`` so the value in the file is the
value HELIOS evaluates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from helios.clock import from_iso8601_utc
from helios.contracts._tokens import Instrument
from helios.contracts.market_fact import MarketFactFrame
from helios.contracts.timeframe import Timeframe
from helios.contracts.window import MarketFactWindow
from helios.errors import ContractViolationError

#: The fixture document format this loader understands.
FIXTURE_SCHEMA_VERSION = "helios.hermes_fixture/1.0.0"

_REQUIRED_DOCUMENT_KEYS = (
    "fixture_schema_version",
    "fixture_id",
    "description",
    "instrument",
    "timeframe",
    "fact_schema_version",
    "source",
    "reference_now_utc",
    "frames",
)


@dataclass(frozen=True, slots=True)
class HermesFixture:
    """One loaded fixture: its metadata and its validated window of facts."""

    fixture_id: str
    description: str
    expectations: tuple[str, ...]
    instrument: Instrument
    timeframe: Timeframe
    fact_schema_version: str
    reference_now_utc: datetime
    window: MarketFactWindow

    @property
    def frames(self) -> tuple[MarketFactFrame, ...]:
        return self.window.frames


def read_fixture_document(path: Path | str) -> dict[str, Any]:
    """Read and structurally check a fixture document, without building frames."""
    path = Path(path)
    if not path.is_file():
        raise ContractViolationError("fixture not found", origin=str(path))
    try:
        document = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ContractViolationError(
            "malformed JSON in fixture", origin=str(path), detail=str(exc)
        ) from exc
    if not isinstance(document, Mapping):
        raise ContractViolationError(
            "a fixture document must be a mapping", origin=str(path)
        )
    missing = [key for key in _REQUIRED_DOCUMENT_KEYS if key not in document]
    if missing:
        raise ContractViolationError(
            "fixture document is missing required keys",
            origin=str(path),
            missing=sorted(missing),
        )
    declared = document["fixture_schema_version"]
    if declared != FIXTURE_SCHEMA_VERSION:
        raise ContractViolationError(
            "unknown fixture_schema_version",
            origin=str(path),
            received=declared,
            supported=FIXTURE_SCHEMA_VERSION,
        )
    if not isinstance(document["frames"], Sequence) or not document["frames"]:
        raise ContractViolationError(
            "fixture document must contain at least one frame", origin=str(path)
        )
    return dict(document)


def load_fixture_frames(
    path: Path | str, *, accepted_schema_versions: Optional[Iterable[str]] = None
) -> tuple[MarketFactFrame, ...]:
    """Build validated frames from a fixture, in file order.

    Frames are NOT re-sorted: a fixture that lists facts out of order is
    malformed input and must fail when a window is built from it.
    """
    path = Path(path)
    document = read_fixture_document(path)
    fact_schema_version = str(document["fact_schema_version"])
    if accepted_schema_versions is not None:
        accepted = tuple(accepted_schema_versions)
        if fact_schema_version not in accepted:
            raise ContractViolationError(
                "fixture declares a market-fact schema version this deployment "
                "is not configured to accept",
                origin=str(path),
                received=fact_schema_version,
                accepted=list(accepted),
            )
    source = document["source"]
    frames: list[MarketFactFrame] = []
    for index, raw in enumerate(document["frames"]):
        if not isinstance(raw, Mapping):
            raise ContractViolationError(
                "fixture frame must be a mapping", origin=str(path), index=index
            )
        record = dict(raw)
        for key in ("timestamp_utc", "candle", "indicators", "observed_at_utc", "ingested_at_utc"):
            if key not in record:
                raise ContractViolationError(
                    "fixture frame is missing a required key",
                    origin=str(path),
                    index=index,
                    key=key,
                )
        try:
            frames.append(
                MarketFactFrame(
                    instrument=record.get("instrument", document["instrument"]),
                    timeframe=record.get("timeframe", document["timeframe"]),
                    timestamp_utc=record["timestamp_utc"],
                    candle=record["candle"],
                    indicators=record["indicators"],
                    provenance={
                        "source": record.get("source", source),
                        "schema_version": record.get(
                            "fact_schema_version", fact_schema_version
                        ),
                        "observed_at_utc": record["observed_at_utc"],
                        "ingested_at_utc": record["ingested_at_utc"],
                    },
                )
            )
        except ContractViolationError as exc:
            raise ContractViolationError(
                exc.message, origin=str(path), frame_index=index, **dict(exc.context)
            ) from exc
    return tuple(frames)


def load_fixture(
    path: Path | str, *, accepted_schema_versions: Optional[Iterable[str]] = None
) -> HermesFixture:
    """Load a fixture into a validated, ordered window."""
    path = Path(path)
    document = read_fixture_document(path)
    frames = load_fixture_frames(path, accepted_schema_versions=accepted_schema_versions)
    try:
        window = MarketFactWindow(frames)
    except ContractViolationError as exc:
        raise ContractViolationError(
            exc.message, origin=str(path), **dict(exc.context)
        ) from exc
    expectations = tuple(str(item) for item in document.get("expectations", ()))
    return HermesFixture(
        fixture_id=str(document["fixture_id"]),
        description=str(document["description"]),
        expectations=expectations,
        instrument=Instrument(str(document["instrument"])),
        timeframe=Timeframe.parse(document["timeframe"]),
        fact_schema_version=str(document["fact_schema_version"]),
        reference_now_utc=from_iso8601_utc(str(document["reference_now_utc"])),
        window=window,
    )
