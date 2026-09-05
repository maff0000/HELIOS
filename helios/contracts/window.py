"""Immutable, ordered per-(instrument, timeframe) lookback window.

A window is the deterministic view of recent history a strategy evaluates
against. It is frozen: a strategy receives one, reads it, and structurally
cannot alter what any other strategy will see. That is enforced rather than
asserted — the ONE window object is handed by reference to every sibling
evaluating the same facts, so ``__slots__`` alone (which only bounds the set
of attribute NAMES) would leave an ordinary ``window._frames = ...`` free to
rewrite what every other strategy is about to read. Assignment and deletion
are both refused, following :class:`~helios.contracts._fields.FrozenMapping`.

Ordering is strictly ascending by bar-open instant. Gaps are permitted —
markets close — but duplicates and out-of-order frames are malformed input and
fail loudly, because they would make evaluation order-dependent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator, Optional, Sequence

from helios.contracts._tokens import Instrument
from helios.contracts.market_fact import MarketFactFrame
from helios.contracts.timeframe import Timeframe
from helios.errors import ContractViolationError, MissingFactError


class MarketFactWindow:
    """An ordered, immutable sequence of frames for one instrument+timeframe."""

    __slots__ = ("_frames", "_instrument", "_timeframe")

    def __init__(self, frames: Iterable[MarketFactFrame]) -> None:
        ordered = tuple(frames)
        if not ordered:
            raise ContractViolationError(
                "a market-fact window must contain at least one frame; "
                "an empty window would be indistinguishable from absent history"
            )
        for index, frame in enumerate(ordered):
            if not isinstance(frame, MarketFactFrame):
                raise ContractViolationError(
                    "market-fact window accepts MarketFactFrame values only",
                    index=index,
                    received_type=type(frame).__name__,
                )
        first = ordered[0]
        for index, frame in enumerate(ordered):
            if frame.instrument != first.instrument:
                raise ContractViolationError(
                    "market-fact window mixes instruments",
                    index=index,
                    expected=str(first.instrument),
                    received=str(frame.instrument),
                )
            if frame.timeframe is not first.timeframe:
                raise ContractViolationError(
                    "market-fact window mixes timeframes",
                    index=index,
                    expected=first.timeframe.code,
                    received=frame.timeframe.code,
                )
        for index in range(1, len(ordered)):
            previous = ordered[index - 1].timestamp_utc
            current = ordered[index].timestamp_utc
            if current == previous:
                raise ContractViolationError(
                    "market-fact window contains a duplicate timestamp",
                    index=index,
                    timestamp_utc=current.isoformat(),
                )
            if current < previous:
                raise ContractViolationError(
                    "market-fact window is not in ascending time order",
                    index=index,
                    previous_timestamp_utc=previous.isoformat(),
                    timestamp_utc=current.isoformat(),
                )
        # object.__setattr__ because this instance refuses ordinary assignment
        # from the moment it exists; see __setattr__ below.
        object.__setattr__(self, "_frames", ordered)
        object.__setattr__(self, "_instrument", first.instrument)
        object.__setattr__(self, "_timeframe", first.timeframe)

    def __setattr__(self, name: str, value: object) -> None:
        raise ContractViolationError(
            "a market-fact window is immutable; build a new window instead",
            attribute=name,
        )

    def __delattr__(self, name: str) -> None:
        raise ContractViolationError(
            "a market-fact window is immutable; build a new window instead",
            attribute=name,
        )

    @property
    def instrument(self) -> Instrument:
        return self._instrument

    @property
    def timeframe(self) -> Timeframe:
        return self._timeframe

    @property
    def frames(self) -> tuple[MarketFactFrame, ...]:
        """The frames, oldest first. Already immutable."""
        return self._frames

    @property
    def latest(self) -> MarketFactFrame:
        """The most recent frame in the window."""
        return self._frames[-1]

    @property
    def oldest(self) -> MarketFactFrame:
        return self._frames[0]

    @property
    def latest_timestamp_utc(self) -> datetime:
        return self._frames[-1].timestamp_utc

    def __len__(self) -> int:
        return len(self._frames)

    def __iter__(self) -> Iterator[MarketFactFrame]:
        return iter(self._frames)

    def __getitem__(self, index: int) -> MarketFactFrame:
        return self._frames[index]

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"MarketFactWindow({self._instrument}, {self._timeframe.code}, "
            f"{len(self._frames)} frames, latest={self.latest_timestamp_utc.isoformat()})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MarketFactWindow):
            return NotImplemented
        return self._frames == other._frames

    def __hash__(self) -> int:
        return hash(self._frames)

    def lookback(self, count: int) -> tuple[MarketFactFrame, ...]:
        """The most recent ``count`` frames, oldest first, failing loudly if short.

        A strategy that declares it needs 200 bars must not silently evaluate
        against 12.
        """
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ContractViolationError(
                "lookback count must be a positive integer", count=repr(count)
            )
        if count > len(self._frames):
            raise MissingFactError(
                "insufficient history for the requested lookback",
                instrument=str(self._instrument),
                timeframe=self._timeframe.code,
                requested=count,
                available=len(self._frames),
            )
        return self._frames[-count:]

    def previous(self, offset: int = 1) -> MarketFactFrame:
        """The frame ``offset`` bars before the latest one."""
        return self.lookback(offset + 1)[0]

    def completed_frames(self) -> tuple[MarketFactFrame, ...]:
        """Only frames HERMES marked ``complete``.

        Strategies that must not act on a forming bar filter here explicitly
        rather than assuming completeness.
        """
        return tuple(frame for frame in self._frames if frame.candle.complete)

    def extended_with(
        self, frame: MarketFactFrame, *, max_length: Optional[int] = None
    ) -> "MarketFactWindow":
        """Return a NEW window with ``frame`` appended; this one is unchanged."""
        frames: Sequence[MarketFactFrame] = self._frames + (frame,)
        if max_length is not None:
            if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 1:
                raise ContractViolationError(
                    "max_length must be a positive integer", max_length=repr(max_length)
                )
            frames = tuple(frames)[-max_length:]
        return MarketFactWindow(frames)
