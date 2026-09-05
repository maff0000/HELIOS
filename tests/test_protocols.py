"""The evaluation interface later work items implement, and strategy isolation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from helios.contracts import (
    Direction,
    EnvelopeKind,
    InputFreshness,
    SemanticRole,
    StrategyIdentity,
    StrategyState,
    StrategyStateEnvelope,
    Validity,
    advance_lifecycle,
    assess_frame,
)
from helios.errors import MissingFactError
from helios.protocols import EvaluationContext, RequiredInput, StrategyEvaluator
from tests.conftest import make_window, utc

EVALUATED_AT = utc("2026-01-05T16:01:00Z")


def context(policy, **overrides):
    fields = dict(
        instrument="XAU_USD",
        evaluated_at_utc=EVALUATED_AT,
        windows={SemanticRole("CONTEXT"): make_window(4)},
        parameters={"min_separation": Decimal("0.25")},
        freshness_policy=policy,
        previous=None,
    )
    fields.update(overrides)
    return EvaluationContext(**fields)


class ProbeEvaluator:
    """A minimal conforming evaluator. It is a contract exercise, not a strategy."""

    def __init__(self, identity: StrategyIdentity) -> None:
        self._identity = identity

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    def required_inputs(self):
        return (
            RequiredInput(
                role=SemanticRole("CONTEXT"),
                timeframe=make_window(1).timeframe,
                lookback=2,
                fields=("close", "ema_50"),
            ),
        )

    def evaluate(self, ctx: EvaluationContext) -> StrategyStateEnvelope:
        window = ctx.window_for("CONTEXT")
        verdict = assess_frame(window.latest, ctx.freshness_policy, now_utc=ctx.evaluated_at_utc)
        lifecycle = advance_lifecycle(None, StrategyState.MATCHED, ctx.evaluated_at_utc)
        return StrategyStateEnvelope.with_lifecycle(
            lifecycle,
            kind=EnvelopeKind.ATOMIC,
            strategy_id=self._identity.strategy_id,
            strategy_version=self._identity.strategy_version,
            instrument=ctx.instrument,
            timeframe=window.timeframe,
            semantic_role="CONTEXT",
            state=StrategyState.MATCHED,
            direction=Direction.LONG,
            strength="0.5",
            evidence={"latest_close": str(window.latest.fact("close"))},
            validity=Validity(valid_from_utc=ctx.evaluated_at_utc),
            inputs=(InputFreshness.from_verdict(verdict, semantic_role="CONTEXT"),),
        )


def test_a_conforming_evaluator_satisfies_the_protocol():
    probe = ProbeEvaluator(StrategyIdentity("golden_cross", "1.0.0"))
    assert isinstance(probe, StrategyEvaluator)


def test_an_evaluator_produces_the_normalised_envelope(policy):
    probe = ProbeEvaluator(StrategyIdentity("golden_cross", "1.0.0"))
    envelope = probe.evaluate(context(policy))
    assert isinstance(envelope, StrategyStateEnvelope)
    assert str(envelope.strategy_id) == "golden_cross"


def test_the_same_context_yields_identical_output(policy):
    probe = ProbeEvaluator(StrategyIdentity("golden_cross", "1.0.0"))
    ctx = context(policy)
    assert probe.evaluate(ctx).to_canonical_json() == probe.evaluate(ctx).to_canonical_json()


def test_an_unbound_role_fails_loudly(policy):
    with pytest.raises(MissingFactError) as caught:
        context(policy).window_for("TRIGGER")
    assert caught.value.context["bound_roles"] == ["CONTEXT"]


def test_an_undeclared_parameter_fails_loudly(policy):
    with pytest.raises(MissingFactError):
        context(policy).parameter("unknown_parameter")


def test_a_declared_parameter_is_readable(policy):
    assert context(policy).parameter("min_separation") == Decimal("0.25")


def test_the_context_is_immutable(policy):
    ctx = context(policy)
    with pytest.raises(Exception):
        ctx.instrument = "EUR_USD"  # type: ignore[misc]
    with pytest.raises(Exception):
        ctx.windows[SemanticRole("TRIGGER")] = make_window(2)  # type: ignore[index]
    with pytest.raises(Exception):
        ctx.parameters["injected"] = 1  # type: ignore[index]


def test_one_strategy_cannot_disturb_another_sharing_the_same_facts(policy):
    """Isolation: shared inputs are frozen, so there is nothing to corrupt."""
    shared = make_window(4)
    first = ProbeEvaluator(StrategyIdentity("golden_cross", "1.0.0"))
    second = ProbeEvaluator(StrategyIdentity("range_breakout", "1.0.0"))
    ctx = context(policy, windows={SemanticRole("CONTEXT"): shared})

    before = second.evaluate(ctx).to_canonical_json()
    first_envelope = first.evaluate(ctx)
    with pytest.raises(Exception):
        shared.frames[0].candle.close = Decimal("1")  # type: ignore[misc]
    with pytest.raises(Exception):
        first_envelope.state = StrategyState.INVALID  # type: ignore[misc]
    assert second.evaluate(ctx).to_canonical_json() == before


def test_a_failing_evaluator_cannot_corrupt_another(policy):
    class Broken(ProbeEvaluator):
        def evaluate(self, ctx):
            raise RuntimeError("this evaluator is broken")

    ctx = context(policy)
    healthy = ProbeEvaluator(StrategyIdentity("golden_cross", "1.0.0"))
    baseline = healthy.evaluate(ctx).to_canonical_json()
    with pytest.raises(RuntimeError):
        Broken(StrategyIdentity("range_breakout", "1.0.0")).evaluate(ctx)
    assert healthy.evaluate(ctx).to_canonical_json() == baseline


def test_previous_state_is_the_only_history_a_strategy_sees(policy):
    probe = ProbeEvaluator(StrategyIdentity("golden_cross", "1.0.0"))
    previous = probe.evaluate(context(policy))
    ctx = context(policy, previous=previous)
    assert ctx.previous is previous
    assert isinstance(ctx.previous, StrategyStateEnvelope)
