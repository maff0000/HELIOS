"""The one decimal context every HELIOS derivation is computed in.

HELIOS promises identical published state for identical ordered inputs. Decimal
arithmetic in Python is performed under a **thread-local ambient context** that
any library, host process or embedding application may replace. Under a coarse
installed context the same expression does not merely produce different digits:
``(numerator / denominator).quantize(...)`` raises ``InvalidOperation`` outright
once the quantised result needs more digits than the ambient precision allows.
Either outcome is a promise broken by state HELIOS does not control.

So every derivation HELIOS performs on decimal facts is computed inside
:data:`ARITHMETIC_CONTEXT` — a fixed precision and a fixed rounding mode — and
never inside whatever the process happens to have installed. The context is
declared once, here, so the atomic strategies, the framework they sit on and the
composition layer all share the same guarantee without one importing another.

What is deliberately NOT solved here: **rendering**. Turning a decimal into the
bytes FALCON receives is the contract layer's job and carries the same guarantee
independently, by performing no decimal operation at all — see
``helios.contracts.serialisation.canonical_decimal`` and ``docs/CONTRACTS.md``
§3.3. Two independent mechanisms, because the two problems are independent: this
module fixes what is *computed*, that one fixes what is *written*.

Nothing here is configuration. The precision is a correctness property of the
engine — two HELIOS processes must agree — not an operational choice a
deployment may make, so it is stated in source rather than injected.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Iterator

#: Precision every HELIOS decimal derivation is computed at.
#:
#: 34 significant digits is IEEE 754 ``decimal128``: far more than any price,
#: indicator or ratio HELIOS handles needs, so no intermediate result is ever
#: silently rounded, while the value is still explicit and finite rather than
#: "whatever the host installed". ``ROUND_HALF_EVEN`` is the IEEE default and
#: is unbiased, so a value exactly between two representations does not drift
#: in one direction across a long replay.
ARITHMETIC_PRECISION = 34

#: The fixed context. Traps are left at their defaults, so a genuinely
#: undefined operation (division by zero, an invalid operation) still raises
#: rather than yielding a quiet ``NaN`` that would flow into published state.
ARITHMETIC_CONTEXT = Context(prec=ARITHMETIC_PRECISION, rounding=ROUND_HALF_EVEN)


@contextmanager
def deterministic_arithmetic() -> Iterator[Context]:
    """Compute inside :data:`ARITHMETIC_CONTEXT`, whatever the caller installed.

    ``decimal`` contexts are thread-local and a new thread starts from the
    interpreter's default rather than inheriting its parent's, so a concurrent
    evaluation and a sequential one would otherwise not even be computing under
    the same rules. Entering this context manager makes both identical.
    """
    with localcontext(ARITHMETIC_CONTEXT) as context:
        yield context


def quantise(value: Decimal, quantum: Decimal) -> Decimal:
    """Quantise ``value`` to ``quantum`` deterministically.

    The quantisation itself is the operation that fails loudly under a coarse
    ambient context — ``Decimal('0.75').quantize(Decimal('0.0001'))`` raises
    ``InvalidOperation`` under ``prec=3`` because the result needs four digits.
    Performing it here means no caller has to remember that.
    """
    with deterministic_arithmetic():
        return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


__all__ = [
    "ARITHMETIC_CONTEXT",
    "ARITHMETIC_PRECISION",
    "deterministic_arithmetic",
    "quantise",
]
