"""Determinism: identical ordered inputs produce identical published bytes."""

from __future__ import annotations

import decimal

from helios.contracts import (
    Direction,
    EnvelopeKind,
    InputFreshness,
    StrategyState,
    StrategyStateEnvelope,
    Validity,
    advance_lifecycle,
    assess_frame,
)
from helios.hermes import load_fixture
from tests.conftest import make_window, utc


def build(window, policy, evaluated_at):
    """A deterministic function of (window, policy, instant) alone.

    Note what it cannot see: there is no argument through which downstream
    execution activity could reach it.
    """
    verdict = assess_frame(window.latest, policy, now_utc=evaluated_at)
    lifecycle = advance_lifecycle(None, StrategyState.MATCHED, evaluated_at)
    return StrategyStateEnvelope.with_lifecycle(
        lifecycle,
        kind=EnvelopeKind.ATOMIC,
        strategy_id="golden_cross",
        strategy_version="1.0.0",
        instrument=window.instrument,
        timeframe=window.timeframe,
        semantic_role="CONTEXT",
        state=StrategyState.MATCHED,
        direction=Direction.LONG,
        strength="0.5",
        evidence={"latest_close": str(window.latest.fact("close"))},
        validity=Validity(valid_from_utc=evaluated_at),
        inputs=(InputFreshness.from_verdict(verdict, semantic_role="CONTEXT"),),
    )


def test_identical_inputs_produce_identical_bytes(policy):
    evaluated_at = utc("2026-01-05T16:01:00Z")
    first = build(make_window(4), policy, evaluated_at)
    second = build(make_window(4), policy, evaluated_at)
    assert first.to_canonical_json() == second.to_canonical_json()


def test_repeated_serialisation_of_one_envelope_is_stable(policy):
    envelope = build(make_window(4), policy, utc("2026-01-05T16:01:00Z"))
    assert len({envelope.to_canonical_json() for _ in range(10)}) == 1


def test_a_different_input_produces_different_bytes(policy):
    evaluated_at = utc("2026-01-05T16:01:00Z")
    baseline = build(make_window(4), policy, evaluated_at)
    longer = build(make_window(5), policy, utc("2026-01-05T20:01:00Z"))
    assert baseline.to_canonical_json() != longer.to_canonical_json()


def test_replaying_a_fixture_is_reproducible(policy, fixture_root):
    """Deterministic replay across two independent loads of the same facts."""
    path = fixture_root / "xau_usd" / "xau_usd_h4.json"
    first = load_fixture(path)
    second = load_fixture(path)
    assert first.window == second.window
    assert build(first.window, policy, first.reference_now_utc).to_canonical_json() == build(
        second.window, policy, second.reference_now_utc
    ).to_canonical_json()


def test_windows_are_hashable_and_compare_by_value():
    assert hash(make_window(3)) == hash(make_window(3))
    assert make_window(3) == make_window(3)
    assert make_window(3) != make_window(4)


def test_published_bytes_are_unchanged_under_a_hostile_decimal_context(policy):
    """A host application's decimal context must not reach FALCON.

    HELIOS does not own the process it runs in. If any library or host code
    installs its own decimal context, the envelope HELIOS already holds must
    still publish to the same bytes.
    """
    envelope = build(make_window(4), policy, utc("2026-01-05T16:01:00Z"))
    baseline = envelope.to_canonical_json()
    for precision, rounding in (
        (1, decimal.ROUND_FLOOR),
        (4, decimal.ROUND_HALF_EVEN),
        (200, decimal.ROUND_CEILING),
    ):
        with decimal.localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            assert envelope.to_canonical_json() == baseline
