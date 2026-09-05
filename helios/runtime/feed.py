"""The ordered market-fact feed the running service evaluates against.

HELIOS v1 ships **no live HERMES client** — no database driver, no connection
string, no credential (``docs/INTEGRATION.md`` §2). Facts arrive as validated
:class:`~helios.contracts.market_fact.MarketFactFrame` values, and for v1 they
arrive from checked-in HERMES fact documents. What this module adds on top of
:mod:`helios.hermes` is the one thing a *running* service needs and a static
fixture does not: **an order in time**.

The idea is small and entirely data-driven. Every fact document declares the
instant its facts should be judged against (``reference_now_utc``), and in every
canonical document the newest bar is exactly one publication delay past its own
close. That delay is therefore a fact of the feed, not a constant in HELIOS
source: read it from the documents, insist every document in the set agrees on
it, and the whole ordered replay follows —

* a frame is **available** at instant ``T`` when ``close + delay <= T``;
* the service evaluates at ``close + delay`` of every bar of the **finest**
  timeframe in the set, because that is the resolution at which this
  deployment's facts actually change;
* at each of those instants every timeframe is presented as the window of
  facts that had arrived by then, and nothing later.

Which means a replay never shows a strategy a fact from its own future, and two
replays of one feed are identical by construction.

Nothing here decides *whether* a fact is fresh enough to evaluate against. That
is the configured :class:`~helios.contracts.freshness.FreshnessPolicy`'s
judgement, applied per strategy at evaluation time, and it is published in every
envelope. The feed only says what had arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Sequence

from helios.clock import ensure_utc
from helios.contracts._tokens import Instrument
from helios.contracts.market_fact import MarketFactFrame
from helios.contracts.timeframe import Timeframe
from helios.contracts.window import MarketFactWindow
from helios.errors import ContractViolationError
from helios.hermes.fixture_source import HermesFixture, load_fixture

#: File suffixes a fact document may use.
FACT_DOCUMENT_SUFFIXES: tuple[str, ...] = (".json",)


@dataclass(frozen=True, slots=True)
class FeedSeries:
    """One timeframe's ordered facts, and where they came from."""

    timeframe: Timeframe
    origin: str
    document_id: str
    description: str
    expectations: tuple[str, ...]
    frames: tuple[MarketFactFrame, ...]

    def available_at(self, instant: datetime, *, delay: timedelta) -> tuple[MarketFactFrame, ...]:
        """Every frame that had arrived by ``instant``, oldest first."""
        return tuple(
            frame for frame in self.frames if frame.close_time_utc + delay <= instant
        )


@dataclass(frozen=True, slots=True)
class MarketFactFeed:
    """An ordered, multi-timeframe feed of HERMES facts for one instrument."""

    instrument: Instrument
    publication_delay: timedelta
    series: Mapping[Timeframe, FeedSeries]

    def __post_init__(self) -> None:
        object.__setattr__(self, "series", MappingProxyType(dict(self.series)))

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """Every timeframe in the feed, finest first."""
        return tuple(sorted(self.series))

    @property
    def finest(self) -> Timeframe:
        """The timeframe whose bars set the evaluation cadence."""
        return self.timeframes[0]

    def instants(
        self, minimum_frames: Optional[Mapping[Timeframe, int]] = None
    ) -> tuple[datetime, ...]:
        """Every instant this feed can be evaluated at, in order.

        One instant per bar of the finest timeframe, at the moment that bar's
        facts arrive. ``minimum_frames`` — how much history each timeframe's
        declared consumers need — trims the front of the replay to the first
        instant at which every timeframe can actually satisfy what was declared
        of it. That is a definition-driven answer, not a configured one: a
        strategy that declared it needs 24 bars must never evaluate against 12,
        and starting a replay where it would have to is manufacturing a failure
        rather than proving anything.
        """
        wanted = dict(minimum_frames or {})
        instants: list[datetime] = []
        for frame in self.series[self.finest].frames:
            instant = frame.close_time_utc + self.publication_delay
            if all(
                len(series.available_at(instant, delay=self.publication_delay))
                >= wanted.get(timeframe, 1)
                for timeframe, series in self.series.items()
            ):
                instants.append(instant)
        return tuple(instants)

    def windows_at(self, instant: datetime) -> Mapping[Timeframe, MarketFactWindow]:
        """The facts each timeframe had delivered by ``instant``.

        A timeframe that had delivered nothing yet is absent from the mapping
        rather than present and empty: "no window" is what
        :class:`~helios.protocols.EvaluationContext` and the freshness rules
        already have a loud answer for, and an empty window is not a thing the
        contract permits.
        """
        moment = ensure_utc(instant, field="instant")
        windows: dict[Timeframe, MarketFactWindow] = {}
        for timeframe, series in self.series.items():
            frames = series.available_at(moment, delay=self.publication_delay)
            if frames:
                windows[timeframe] = MarketFactWindow(frames)
        return MappingProxyType(windows)

    def describe(self) -> dict[str, object]:
        """A log-safe description of what this feed holds."""
        return {
            "feed_instrument": str(self.instrument),
            "feed_publication_delay_seconds": int(
                self.publication_delay.total_seconds()
            ),
            "feed_series": [
                {
                    "timeframe": timeframe.code,
                    "document_id": self.series[timeframe].document_id,
                    "frames": len(self.series[timeframe].frames),
                    "earliest_bar_open_utc": self.series[timeframe].frames[0].timestamp_utc,
                    "latest_bar_close_utc": self.series[timeframe].frames[-1].close_time_utc,
                }
                for timeframe in self.timeframes
            ],
        }


def fact_document_paths(directory: Path | str) -> tuple[Path, ...]:
    """Every fact document in a feed directory, in a deterministic order."""
    root = Path(directory)
    if not root.is_dir():
        raise ContractViolationError("market-fact feed directory not found", origin=str(root))
    return tuple(
        sorted(
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in FACT_DOCUMENT_SUFFIXES
        )
    )


def load_feed(
    directory: Path | str,
    *,
    instrument: Instrument | str,
    accepted_schema_versions: Optional[Iterable[str]] = None,
) -> MarketFactFeed:
    """Load an ordered feed from a directory of HERMES fact documents.

    Every refusal below is loud and names the offending document. A feed that
    is internally inconsistent — two documents for one timeframe, a document
    for another instrument, documents that disagree about when facts arrive —
    would produce a replay whose meaning nobody could state, and HELIOS
    publishes no state it cannot account for.
    """
    subject = instrument if isinstance(instrument, Instrument) else Instrument(str(instrument))
    paths = fact_document_paths(directory)
    if not paths:
        raise ContractViolationError(
            "market-fact feed directory holds no fact documents",
            origin=str(directory),
            suffixes=list(FACT_DOCUMENT_SUFFIXES),
        )

    loaded: list[tuple[Path, HermesFixture]] = [
        (path, load_fixture(path, accepted_schema_versions=accepted_schema_versions))
        for path in paths
    ]

    series: dict[Timeframe, FeedSeries] = {}
    delays: dict[str, timedelta] = {}
    for path, document in loaded:
        if document.instrument != subject:
            raise ContractViolationError(
                "market-fact feed mixes instruments; a deployment evaluates one "
                "subject and HELIOS will not guess which facts belong to it",
                origin=str(path),
                expected=str(subject),
                received=str(document.instrument),
            )
        if document.timeframe in series:
            raise ContractViolationError(
                "two fact documents supply the same timeframe; the ordered feed "
                "for a timeframe must have one source",
                origin=str(path),
                timeframe=document.timeframe.code,
                previous_origin=series[document.timeframe].origin,
            )
        delay = document.reference_now_utc - document.window.latest.close_time_utc
        if delay < timedelta(0):
            raise ContractViolationError(
                "fact document is judged against an instant before its newest bar "
                "closed; a feed cannot deliver a fact before it exists",
                origin=str(path),
                timeframe=document.timeframe.code,
            )
        delays[str(path)] = delay
        series[document.timeframe] = FeedSeries(
            timeframe=document.timeframe,
            origin=str(path),
            document_id=document.fixture_id,
            description=document.description,
            expectations=document.expectations,
            frames=document.frames,
        )

    distinct = sorted({int(value.total_seconds()) for value in delays.values()})
    if len(distinct) != 1:
        raise ContractViolationError(
            "fact documents disagree about how long after a bar closes its facts "
            "arrive; one feed has one cadence, and HELIOS will not average them",
            origin=str(directory),
            delays_seconds=distinct,
            documents={name: int(value.total_seconds()) for name, value in delays.items()},
        )

    return MarketFactFeed(
        instrument=subject,
        publication_delay=timedelta(seconds=distinct[0]),
        series=series,
    )


def minimum_frames_for(
    requirements: Sequence[tuple[Timeframe, int]]
) -> Mapping[Timeframe, int]:
    """Fold declared lookbacks into the deepest history each timeframe owes."""
    wanted: dict[Timeframe, int] = {}
    for timeframe, lookback in requirements:
        wanted[timeframe] = max(wanted.get(timeframe, 0), int(lookback))
    return MappingProxyType(wanted)
