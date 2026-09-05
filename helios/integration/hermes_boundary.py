"""The HERMES input boundary.

HERMES is the market-fact authority; HELIOS consumes what it publishes. This
module is the single, loud acceptance path for that input, and a
machine-readable description of exactly what HELIOS requires.

This is a **data contract, not a client.** There is no connection string, no
hostname, no port and no credential anywhere in this package, and there is no
live HERMES client in HELIOS v1: facts arrive as validated
:class:`~helios.contracts.market_fact.MarketFactFrame` values, from the
fixture source today and from whatever transport a later work item chooses.
The rules below do not change when the transport does.

Nothing is silently defaulted, silently zeroed or silently re-ordered. Every
rejection names the instrument, the timeframe and the offending instant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from helios.contracts._tokens import SemanticRole
from helios.contracts.freshness import (
    FreshnessPolicy,
    FreshnessVerdict,
    assess_frame,
    require_fresh_frame,
)
from helios.contracts.market_fact import (
    CANDLE_FIELDS,
    INDICATOR_FIELDS,
    MARKET_FACT_SCHEMA_VERSION,
    MarketFactFrame,
    Provenance,
)
from helios.contracts.output import InputFreshness
from helios.contracts.timeframe import ALL_TIMEFRAMES
from helios.contracts.window import MarketFactWindow
from helios.errors import ContractViolationError

#: The provenance fields every fact must carry, derived from the contract
#: model so this description cannot drift from what is enforced.
PROVENANCE_FIELDS: tuple[str, ...] = tuple(Provenance.model_fields)

#: What HELIOS refuses, in the order the checks are applied.
INPUT_REJECTION_RULES: tuple[str, ...] = (
    "a binary float anywhere a price or indicator value is expected",
    "a field HELIOS does not know (the fact contract is a closed world)",
    "a candle whose high is below its low, or whose open/close sits outside "
    "the high/low range",
    "a negative volume, or an rsi_14 outside 0..100",
    "a naive timestamp, or one that is not a plausible bar-open instant for "
    "its timeframe",
    "a timeframe HELIOS does not support",
    "provenance whose ingested_at_utc precedes its observed_at_utc",
    "a window mixing instruments or timeframes",
    "duplicate or out-of-order frames (they are never silently re-sorted)",
    "a fact whose schema_version this deployment is not configured to accept",
    "a fact older than the configured freshness limit",
    "an incomplete bar, unless the configured policy allows one",
    "a required indicator that is absent (never substituted with zero)",
)


def describe_input_contract() -> dict[str, Any]:
    """A machine-readable statement of what HELIOS requires from HERMES.

    Every list here is derived from the contract models, so a change to the
    contract changes this description automatically.
    """
    return {
        "fact_schema_version": MARKET_FACT_SCHEMA_VERSION,
        "timeframes": [
            {
                "helios_code": timeframe.code,
                "hermes_code": timeframe.hermes_code,
                "seconds": timeframe.seconds,
            }
            for timeframe in ALL_TIMEFRAMES
        ],
        "required_candle_fields": list(CANDLE_FIELDS),
        "optional_indicator_fields": list(INDICATOR_FIELDS),
        "required_provenance_fields": list(PROVENANCE_FIELDS),
        "timestamp_meaning": "bar open instant, UTC, timezone-aware",
        "numeric_encoding": "exact decimal text; binary floats are refused",
        "ordering": "strictly ascending by bar-open instant per "
        "(instrument, timeframe); gaps permitted, duplicates and "
        "out-of-order frames refused",
        "freshness_rule": "age from the bar CLOSE instant must be at most "
        "(timeframe duration * max_age_multiplier) + grace, both configured "
        "externally, with optional per-timeframe absolute overrides",
        "rejects": list(INPUT_REJECTION_RULES),
    }


def accept_market_facts(
    frames: Iterable[MarketFactFrame],
    *,
    policy: FreshnessPolicy,
    now_utc: datetime,
    accepted_schema_versions: Optional[Iterable[str]] = None,
) -> MarketFactWindow:
    """Accept HERMES facts for evaluation, or refuse them loudly.

    Applies, in order: structural window validity (single instrument and
    timeframe, strictly ascending, no duplicates), schema-version acceptance
    for every frame, then freshness and completeness of the newest frame.

    Returns the validated window. Raises
    :class:`~helios.errors.ContractViolationError` for malformed input,
    :class:`~helios.errors.StaleFactError` for input that is well-formed but
    too old or still forming, and
    :class:`~helios.errors.MissingFactError` when nothing was supplied.
    """
    window = MarketFactWindow(frames)
    if accepted_schema_versions is not None:
        accepted = tuple(accepted_schema_versions)
        if not accepted:
            raise ContractViolationError(
                "a deployment must accept at least one HERMES fact schema version"
            )
        for index, frame in enumerate(window.frames):
            declared = frame.provenance.schema_version
            if declared not in accepted:
                raise ContractViolationError(
                    "market fact declares a schema version this deployment is "
                    "not configured to accept",
                    instrument=str(frame.instrument),
                    timeframe=frame.timeframe.code,
                    frame_index=index,
                    timestamp_utc=frame.timestamp_utc.isoformat(),
                    received=declared,
                    accepted=sorted(accepted),
                )
    require_fresh_frame(window.latest, policy, now_utc=now_utc)
    return window


def input_freshness(
    window: MarketFactWindow,
    *,
    policy: FreshnessPolicy,
    now_utc: datetime,
    semantic_role: Optional[str] = None,
) -> InputFreshness:
    """The publishable freshness record for the newest fact in a window.

    This is what lands in the output envelope's ``inputs``, so a FALCON
    consumer can see exactly how fresh the facts behind a state were, and
    under what limit that judgement was made.
    """
    verdict: FreshnessVerdict = assess_frame(window.latest, policy, now_utc=now_utc)
    return InputFreshness.from_verdict(
        verdict,
        semantic_role=SemanticRole(semantic_role) if semantic_role is not None else None,
    )
