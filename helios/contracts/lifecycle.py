"""Deterministic lifecycle timestamps.

The normalised output contract must publish *first matched*, *last matched*,
*active since* and *last evaluated* instants. Deriving those consistently
across every atomic strategy and every chain is contract-level behaviour, not
something each strategy re-implements (and gets subtly wrong).

:func:`advance_lifecycle` is a pure function: the same previous lifecycle,
state and instant always produce the same result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from helios.clock import ensure_utc
from helios.contracts.state import LIVE_STATES, StrategyState


@dataclass(frozen=True, slots=True)
class LifecycleTimestamps:
    """Immutable lifecycle instants for one strategy occurrence.

    * ``first_matched_at_utc`` — when the condition first matched in the
      current occurrence; cleared when the occurrence resolves and the
      strategy returns to DORMANT.
    * ``last_matched_at_utc`` — the most recent evaluation at which the
      condition held (MATCHED / ACTIVE / WEAKENING).
    * ``active_since_utc`` — when the strategy most recently became live.
      Survives an ACTIVE -> WEAKENING -> ACTIVE excursion, because that is one
      continuous activation.
    * ``last_evaluated_at_utc`` — always set; HELIOS publishes proof of
      evaluation even when the state did not change.
    """

    last_evaluated_at_utc: datetime
    first_matched_at_utc: Optional[datetime] = None
    last_matched_at_utc: Optional[datetime] = None
    active_since_utc: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "last_evaluated_at_utc",
            ensure_utc(self.last_evaluated_at_utc, field="last_evaluated_at_utc"),
        )
        for field in ("first_matched_at_utc", "last_matched_at_utc", "active_since_utc"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, ensure_utc(value, field=field))


def advance_lifecycle(
    previous: Optional[LifecycleTimestamps],
    new_state: StrategyState,
    evaluated_at_utc: datetime,
) -> LifecycleTimestamps:
    """Derive the lifecycle timestamps for an evaluation that produced ``new_state``.

    Rules, applied in order and identically for atomic strategies and chains:

    1. ``last_evaluated_at_utc`` is always the evaluation instant.
    2. Entering or remaining in a live state sets ``last_matched_at_utc``.
    3. ``first_matched_at_utc`` is set once per occurrence, on the first live
       evaluation, and preserved for the rest of that occurrence.
    4. ``active_since_utc`` is set when the strategy becomes live and is
       preserved while it stays live.
    5. Returning to ``DORMANT`` clears the occurrence: a new cycle starts with
       no inherited match history. ``INVALID``/``EXPIRED`` deliberately retain
       the history so consumers can see what just ended and when.
    """
    evaluated_at_utc = ensure_utc(evaluated_at_utc, field="evaluated_at_utc")
    was_live = previous is not None and previous.active_since_utc is not None
    is_live = new_state in LIVE_STATES

    if new_state is StrategyState.DORMANT:
        return LifecycleTimestamps(last_evaluated_at_utc=evaluated_at_utc)

    first_matched = previous.first_matched_at_utc if previous else None
    last_matched = previous.last_matched_at_utc if previous else None
    active_since = previous.active_since_utc if previous else None

    if is_live:
        last_matched = evaluated_at_utc
        if first_matched is None:
            first_matched = evaluated_at_utc
        if not was_live or active_since is None:
            active_since = evaluated_at_utc
    else:
        # FORMING, INVALID and EXPIRED are not live: the activation is over.
        active_since = None

    return LifecycleTimestamps(
        last_evaluated_at_utc=evaluated_at_utc,
        first_matched_at_utc=first_matched,
        last_matched_at_utc=last_matched,
        active_since_utc=active_since,
    )
