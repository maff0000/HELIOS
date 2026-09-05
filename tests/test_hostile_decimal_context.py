"""Published bytes must not depend on the host's decimal context.

Python's decimal arithmetic runs under a **thread-local ambient context** that
any library, embedding application or host process may replace. HELIOS promises
identical published state for identical ordered inputs; that promise is only
worth something if it survives a process that installed its own context.

This is a **suite-wide** guard rather than a unit test of one helper. It drives
the real slice — HERMES facts in, atomic strategies evaluated concurrently,
chains composed, canonical payloads published — over the whole ordered feed,
once per hostile context, and asserts that every published byte is identical to
the run under the interpreter's default context and that nothing raised.

Why the whole path and not the arithmetic in isolation: the exposure that
prompted this guard was not a helper anyone was suspicious of. It was
``_strength`` in the chain engine, which quantises a ratio to four places — and
under ``prec=3`` that quantisation does not return different digits, it raises
``InvalidOperation``, so a chain that should publish ``0.7500`` publishes
nothing at all. Only exercising the real path finds the next one of those.

The chosen contexts are deliberately extreme in both directions:

* ``prec=1`` and ``prec=3`` — coarser than the quanta HELIOS publishes at, so a
  quantisation performed under them raises rather than rounds;
* ``prec=4`` — coarse enough to change a published ratio's digits silently;
* ``prec=200`` — far finer, which changes digits the other way;
* directional roundings — ``ROUND_FLOOR``, ``ROUND_CEILING``, ``ROUND_UP``,
  ``ROUND_DOWN`` — which bias every half-way value.
"""

from __future__ import annotations

import decimal
from decimal import Decimal

import pytest

from helios.determinism import ARITHMETIC_CONTEXT, deterministic_arithmetic, quantise
from tests._scenario import replay

#: Contexts no sane deployment installs, and every one of which a dependency
#: legitimately might.
HOSTILE_CONTEXTS = {
    "prec_1": decimal.Context(prec=1),
    "prec_3": decimal.Context(prec=3),
    "prec_4": decimal.Context(prec=4),
    "prec_200": decimal.Context(prec=200),
    "round_floor": decimal.Context(prec=6, rounding=decimal.ROUND_FLOOR),
    "round_ceiling": decimal.Context(prec=9, rounding=decimal.ROUND_CEILING),
    "round_up": decimal.Context(prec=5, rounding=decimal.ROUND_UP),
    "round_down": decimal.Context(prec=5, rounding=decimal.ROUND_DOWN),
}

CONTEXT_NAMES = sorted(HOSTILE_CONTEXTS)


@pytest.fixture(scope="module")
def reference(tmp_path_factory) -> tuple[str, ...]:
    """Every payload one full replay publishes, under the default context."""
    payloads = replay(tmp_path_factory.mktemp("reference") / "status.json")
    assert payloads, "the reference replay published nothing"
    return payloads


def test_the_guard_is_genuinely_hostile():
    """A guard that cannot fail proves nothing.

    Each of these is a failure mode the engine had to be protected from, shown
    on plain decimal arithmetic so the guard's premise is visible rather than
    assumed: a quantisation that raises, a ratio whose digits change, and a
    price comparison that silently moves.
    """
    with decimal.localcontext(HOSTILE_CONTEXTS["prec_3"]):
        with pytest.raises(decimal.InvalidOperation):
            (Decimal(3) / Decimal(4)).quantize(Decimal("0.0001"))
    with decimal.localcontext(HOSTILE_CONTEXTS["prec_4"]):
        assert Decimal(11) / Decimal(13) == Decimal("0.8462")
        assert Decimal(11) / Decimal(13) != Decimal("0.846154")
    with decimal.localcontext(HOSTILE_CONTEXTS["round_floor"]):
        assert Decimal("2408.50") + Decimal("1.2345") != Decimal("2409.7345")


@pytest.mark.parametrize("name", CONTEXT_NAMES)
def test_the_published_bytes_are_unchanged_under_a_hostile_context(
    name, reference, tmp_path
):
    """The whole slice, replayed under somebody else's decimal context.

    Byte equality against the default-context run covers both failure modes at
    once — a raise never reaches the comparison — but they are asserted
    separately below so a regression says which one broke.
    """
    with decimal.localcontext(HOSTILE_CONTEXTS[name]):
        payloads = replay(tmp_path / "status.json")
    assert payloads == reference, f"published bytes changed under {name}"


@pytest.mark.parametrize("name", CONTEXT_NAMES)
def test_nothing_in_the_engine_raises_under_a_hostile_context(name, tmp_path):
    """Determinism is the point; not raising is the floor."""
    with decimal.localcontext(HOSTILE_CONTEXTS[name]):
        payloads = replay(tmp_path / "status.json")
    assert payloads


def test_the_engine_installs_its_own_context_and_restores_the_callers():
    with decimal.localcontext(HOSTILE_CONTEXTS["prec_1"]):
        with deterministic_arithmetic() as context:
            assert context.prec == ARITHMETIC_CONTEXT.prec
            assert context.rounding == ARITHMETIC_CONTEXT.rounding
            assert (Decimal(3) / Decimal(4)).quantize(Decimal("0.0001")) == Decimal(
                "0.7500"
            )
        assert decimal.getcontext().prec == 1, "the caller's context must be restored"


def test_quantising_is_safe_under_a_context_that_could_not_hold_the_result():
    with decimal.localcontext(HOSTILE_CONTEXTS["prec_1"]):
        assert quantise(Decimal("0.75"), Decimal("0.0001")) == Decimal("0.7500")


def test_the_chain_strength_that_prompted_this_guard():
    """The confirmed defect, at the exact call site.

    Three of four components holding is ``0.7500`` — four significant digits,
    which a ``prec=3`` context cannot represent, so the quantisation inside
    ``_strength`` used to raise rather than round.
    """
    from helios.composition.engine import _strength
    from helios.composition.outcomes import (
        ChainAssessment,
        ComponentOutcome,
        ComponentReason,
    )
    from helios.contracts.identity import StrategyIdentity
    from helios.contracts.state import Direction, StrategyState
    from helios.spec.model import DirectionRelationship

    def outcome(satisfied: bool, index: int) -> ComponentOutcome:
        return ComponentOutcome(
            declared=StrategyIdentity("golden_cross", "1.0.0"),
            role=None,
            timeframe=None,
            sequence_index=index,
            relationship=DirectionRelationship.SAME,
            required_states=(StrategyState.MATCHED,),
            envelope=None,
            satisfied=satisfied,
            reason=ComponentReason.SATISFIED if satisfied else ComponentReason.NOT_SUPPLIED,
            detail="constructed for this guard",
        )

    assessment = ChainAssessment(
        satisfied=False,
        direction=Direction.LONG,
        outcomes=tuple(outcome(index < 3, index) for index in range(4)),
        chain_reasons=(),
    )
    assert _strength(assessment) == Decimal("0.7500")
    for name in CONTEXT_NAMES:
        with decimal.localcontext(HOSTILE_CONTEXTS[name]):
            assert _strength(assessment) == Decimal("0.7500"), name


def test_concurrent_and_sequential_evaluation_agree_under_a_hostile_context(tmp_path):
    """The guard's second finding, and a subtler one than the first.

    A ``decimal`` context is thread-local, and a new thread starts from the
    interpreter's default rather than inheriting its parent's. So a hostile
    context installed by a host process reaches an atom evaluated *in the
    caller's thread* and does not reach the same atom evaluated *in a worker
    thread* — meaning ``evaluate_sequentially`` and ``evaluate_concurrently``
    could publish different bytes for identical inputs, which is precisely the
    invariant ``docs/ATOMS.md`` §5 states they hold to.

    The whole-replay guard above cannot see this: it drives the concurrent
    path, where the hostile context never arrives. So the two paths are
    compared directly, against each other and against the default-context run.
    """
    from helios.strategies import evaluate_concurrently, evaluate_sequentially
    from tests._scenario import build_scenario_runtime

    runtime = build_scenario_runtime(tmp_path / "status.json")
    instant = runtime.instants[-1]
    atoms = runtime.strategies.atoms
    context_for = runtime.contexts_at(instant)

    def rendered(outcomes) -> tuple[str, ...]:
        return tuple(outcome.envelope.to_canonical_json() for outcome in outcomes)

    expected = rendered(evaluate_sequentially(atoms, context_for))
    assert expected == rendered(evaluate_concurrently(atoms, context_for))
    for name in CONTEXT_NAMES:
        with decimal.localcontext(HOSTILE_CONTEXTS[name]):
            assert rendered(evaluate_sequentially(atoms, context_for)) == expected, name
            assert rendered(evaluate_concurrently(atoms, context_for)) == expected, name
