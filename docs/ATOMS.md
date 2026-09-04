# HELIOS atomic strategies

This document describes the atomic strategy framework and the six proof atoms
built on it. It is the reference for HSA when writing an atomic package, and
for FALCON when reading the state one publishes.

The contracts these atoms consume and publish are defined in
`docs/CONTRACTS.md`; the market facts they are tested against are described in
`docs/FIXTURES.md`. Nothing here restates those — this document covers what
each strategy *means*.

> The six atoms exist to prove the framework. The PID says so explicitly, and
> says equally explicitly not to build a large strategy library in v1. **None
> of them is production alpha.** Whether any of these conditions is worth
> anything is HSA's question, not HELIOS's.

---

## 1. What an atomic strategy is

One module, one condition, one published state. An atom:

* declares the market facts it reads and the parameters it needs;
* is handed a frozen window of HERMES facts and its own last envelope;
* decides whether its condition holds, in which direction, how strongly, and
  says why;
* knows nothing about any other strategy, and has no way to reach one.

Everything else — freshness, the state machine, lifecycle instants, the
published envelope — is `helios/strategies/base.py`, identically for every
atom. That uniformity is the point: a chain comparing two atoms it knows
nothing about can rely on `MATCHED` meaning the same thing in both.

### 1.1 Consumed facts, not computed indicators

`ema_50`, `ema_200`, `rsi_14` and `atr_14` are **HERMES facts**. HELIOS
consumes them and never recomputes them; there is deliberately no reusable
indicator library in this codebase, and `tests/test_isolation.py` confines
what an atom module may import.

What atoms *do* compute is strategy-local geometry over facts they were
handed: "is this bar's wick more than 0.6 of this bar's range", "what is the
highest high of the declared lookback", "how much did `atr_14` change since
the previous bar". That is intrinsic to a strategy definition, not a market
indicator HERMES should own.

### 1.2 Exactly one semantic input

An atomic package declares **one** input, and the registry refuses more. Which
semantic role it is, and which timeframe fills that role, is the package's own
decision — no atom's source names a role or a timeframe. `golden_cross` binds
`CONTEXT`→H4 in the shipped package and `BIAS`→D1 in a test, with the same
implementation and no change to it.

This is a deliberate v1 boundary rather than a gap in the package format.
Binding several inputs positionally would be ambiguous, and combining facts
across semantic roles is what the composition layer is for. An atom that
genuinely needs two roles is a specification question for HSA, not something
HELIOS should invent an answer to.

### 1.3 The evaluation is a fold

An evaluation is a pure function of `(previous envelope, facts)`. There is no
hidden state on the object, which is why the same context always produces the
same envelope and why concurrent evaluation is safe without a single lock.

Anything an occurrence must remember between bars therefore travels in the
**published evidence** — the level a breakout cleared, the swing a location
was measured against, the run of consecutive holds. Publishing it rather than
hiding it means a consumer can see the exact value the state depends on.

---

## 2. Binding a package to an implementation

`helios/strategies/registry.py`. The binding key is the package's own
`strategy_id`: the package format has no separate "implementation" field, and
adding one would give HSA two ways to name the same thing that could disagree.

Every one of the following is a loud `StrategySpecError` naming the package,
never a default and never a silent skip:

| refused | why |
|---|---|
| a package whose `strategy_id` has no registered implementation | HELIOS will not guess which condition was meant |
| a `CHAIN` package | chains belong to the composition layer |
| a package declaring zero or several inputs | see §1.2 |
| an input that does not declare a fact the atom reads | the atom would read an undeclared fact |
| a `lookback` shorter than the atom's minimum | a strategy must not evaluate on less history than it needs |
| a parameter the atom does not use | a misspelt parameter must never become a silent no-op |
| a parameter the atom needs and the package omits | HELIOS supplies no default for a strategy value |
| a parameter whose declared type differs from the one the atom reads | |
| a value outside the atom's own limits, or the package's own declared range | the tighter of the two always wins |
| an atom-specific incoherence | e.g. a range longer than the declared history, or overlapping momentum bounds |
| an edge condition asked to persist for several frames before matching | see §4.4 — the package could never match |

**There is no parameter-override facility.** A promoted
`(strategy_id, strategy_version)` is immutable and is the key CER records
evidence against; a different parameter value is a different definition, and
therefore a new version rather than a runtime argument.

---

## 3. Freshness: configuration is the ceiling, a package may only tighten it

The deployment's `FreshnessPolicy` comes from validated configuration. A
package may make itself **stricter** and never laxer:

* `inputs[].max_age_seconds` is applied only if it is shorter than the
  configured limit for that timeframe;
* `timing.evaluate_on: CLOSED_FRAME` refuses a forming bar even where the
  deployment tolerates one; `EVERY_FRAME` does not force one on a deployment
  that refuses them.

A package cannot talk a deployment into evaluating facts that deployment
considers too old. Missing facts, absent windows, insufficient history and
stale or incomplete bars all raise — nothing is defaulted, zeroed or
substituted. In the multi-strategy path those raises are contained (§5).

---

## 4. The state model

The seven states and their legal transitions are defined in
`docs/CONTRACTS.md` §2. What follows is how an atom's verdict becomes one of
them. The rules are applied in this order, identically for every atom.

1. **A resolved occurrence rearms only through `DORMANT`**, and the two ways
   of resolving rearm differently on purpose.
   * `EXPIRED` means time ran out on a condition that may still be true.
     Rearming at once would re-match what just aged out and make the declared
     expiry meaningless, so an expired occurrence **stays expired for as long
     as its own condition holds in the same direction**.
   * `INVALID` means the condition broke. There is nothing left to hold open,
     so the strategy rearms on the next evaluation. That is what lets a
     reversed condition match the other way shortly afterwards.
2. **Expiry** — a live occurrence whose declared validity window has passed
   becomes `EXPIRED`.
3. **Invalidation** — an atom-declared hard invalidation (a cross back, a
   close through the level) voids a live or forming occurrence: `INVALID`.
4. **The condition holds** — already live: `ACTIVE`, or `WEAKENING` when the
   package enables it and the strength measure fell since the last
   evaluation. Not yet live: `MATCHED` once it has held for the declared
   `min_matched_frames`, `FORMING` until then.
5. **The condition does not hold** — from a live state that is `INVALID`
   (once matched, an occurrence must resolve explicitly). Otherwise `FORMING`
   if the atom reports a declared precondition holding, else `DORMANT`.

### 4.1 What each state means in an envelope

* `direction` — the current bias while live; for `INVALID`/`EXPIRED`, the bias
  of the occurrence that just ended, so a consumer can see *what* was
  invalidated rather than a bare `NEUTRAL`; otherwise the resting direction
  (`NEUTRAL` for a direction-aware strategy with no current bias).
* `strength` — published only while live.
* `explanation` — always two clauses: what the atom saw, then why that
  produced this state. It is the strategy's own account of the state, in its
  own terms and numbers.
* `validity` — `valid_from_utc` is the first match of the occurrence,
  `valid_until_utc` its declared expiry (`null` for `NEVER`), `reason` the
  resolution reason for `INVALID`/`EXPIRED`.
* `evidence` — the facts and derived measures behind the verdict, plus
  `consecutive_hold_frames`.

### 4.2 The cost of rearming through DORMANT

Because a resolved occurrence must pass through `DORMANT`, a strategy that is
invalidated on one bar cannot publish `MATCHED` on the next; the earliest is
the bar after. That is the contract's transition table doing its job — it
keeps the lifecycle auditable — and it is visible in the replays below.

### 4.3 Derived measures are quantised

Every ratio an atom publishes is computed at a fixed precision
(`RATIO_QUANTUM`, six decimal places, half-even) inside an explicit decimal
context, so a caller who had installed a coarser or finer ambient context
cannot change what a strategy computes.

> **Known boundary, outside this work item.** Rendering a decimal to canonical
> JSON is the contract layer's job, and
> `helios.contracts.serialisation.canonical_decimal` calls `Decimal.normalize()`,
> which *does* honour the ambient decimal context. Nothing in HELIOS installs a
> non-default context, so published bytes are stable in practice and the
> cross-process determinism tests pass — but the guarantee is weaker than it
> looks if a host application ever changes the context. This belongs to the
> contract kernel, not to the strategies.

### 4.4 Edge conditions

`golden_cross` matches by *changing*: its condition becomes true on the bar
where the averages swap sides. Such a condition can never accumulate
consecutive holds before going live, so a package asking it to persist for
several frames first describes a strategy that could never match. That is a
specification error and is refused at bind time rather than silently never
firing.

---

## 5. Concurrent evaluation and containment

`helios/strategies/evaluation.py` evaluates many strategies over one set of
facts. Two invariants:

* **Results follow the supplied order, never the completion order**, so a
  concurrent run and a sequential run produce byte-identical output.
* **A strategy that raises is contained.** It resolves to an explicit
  `INVALID` envelope carrying the error type in `evidence` and the reason in
  `explanation`, retaining the lifecycle history of whatever was live before.
  Every sibling still publishes its own correct state; the siblings' bytes are
  identical with and without a failing strategy present.

`INVALID` is the right published state for a failure: it is the contract's own
word for "this state is void". The alternative, `DORMANT`, would assert
"evaluated, nothing holds" when in fact nothing was evaluated — a silent
default of exactly the kind the PID forbids.

> **Deliberate, documented exception.** A containment envelope is published
> **without** consulting the legal transition table, because an evaluation
> failure is not a state change of the strategy's condition. The table governs
> condition-driven transitions, which is all any strategy's own evaluation can
> produce, and every one of those *is* validated against it. Without this
> exception a strategy that was `DORMANT` could not publish a failure at all,
> since `DORMANT -> INVALID` is not a legal condition-driven transition.

Containment covers `evaluate()`. An evaluator's `identity` and
`required_inputs()` are read in the caller's thread before dispatch: an
evaluator that cannot describe itself was never validly built, and that is a
loud failure of the caller's wiring rather than a strategy-level failure.

Isolation is enforced, not trusted. `tests/test_isolation.py` parses every
atom module's imports, imports each atom in a **fresh interpreter** and asserts
no sibling is loaded, checks that no atom module mentions another's name
anywhere in its source, and forces the concurrent path to genuinely overlap
with a thread barrier.

---

## 6. The six proof atoms

Each entry gives the required HERMES inputs, the parameters, the direction
semantics, and the lifecycle over the canonical fixtures. Every stated
sequence is asserted in the tests named at the end of the entry.

Reference packages: `golden_cross`, `range_breakout` and `rejection_wick` are
the packages shipped with the contract kernel in
`fixtures/strategy_packages/valid/`. The three that had none ship beside the
code in `helios/strategies/packages/`.

---

### 6.1 `golden_cross` — golden / death cross

| | |
|---|---|
| **Reads** | `close`, `ema_50`, `ema_200` |
| **History** | 2 bars minimum |
| **Parameters** | `min_separation` (DECIMAL, ≥ 0) — smallest absolute `ema_50`/`ema_200` gap that counts as a genuine cross rather than the two averages brushing against each other |
| **Direction** | `LONG` when `ema_50` crosses above `ema_200`, `SHORT` below |
| **Strength** | `excess / (excess + min_separation)` where `excess = |separation| - min_separation`: 0 at the threshold, 0.5 at one further unit of separation, approaching 1 thereafter. With `min_separation` of 0 the measure degenerates to "any separation is full strength", which is the honest consequence of declaring no minimum |

The condition is a **cross**, not "one average happens to be above the other".
A cold start therefore publishes `DORMANT`, because HELIOS never observed a
side change — it will not claim a match for something that happened before it
was watching.

* `FORMING` — the averages have converged inside `min_separation`; a cross is
  possible but unconfirmed.
* `MATCHED` — the side changed since the previous bar, with enough separation.
* `ACTIVE` — the same side, still clear of the minimum.
* `INVALID` — the averages crossed back, or the separation collapsed inside
  the declared minimum.

**Over `xau_usd_h4`** (separations −3.00, −2.00, −1.00, −0.50, −0.10, then
+0.40 upward, with `min_separation` 0.25):

```
frame  1  2  3  4     5        6        7 .. 11
       DORMANT ×4     FORMING  MATCHED  ACTIVE ×5
```

**Over `xau_usd_h4_death_cross`** (+2.00, +1.00, +0.50, then −0.40):

```
frame  1  2  3     4        5 .. 7
       DORMANT ×3  MATCHED  ACTIVE ×3
```

Tests: `tests/test_atom_golden_cross.py`.

---

### 6.2 `range_breakout` — breakout of the recent range

| | |
|---|---|
| **Reads** | `high`, `low`, `close`, `atr_14` |
| **History** | 2 bars minimum, and at least `lookback_bars` |
| **Parameters** | `lookback_bars` (INTEGER, ≥ 2) — bars examined including the one being evaluated, so the range is measured over `lookback_bars - 1`; `min_atr_multiple` (DECIMAL, ≥ 0) — clearance beyond the range as a multiple of `atr_14` |
| **Direction** | `LONG` above the range high, `SHORT` below the range low |
| **Strength** | `distance / (distance + atr_14)` where `distance` is how far the close sits beyond the cleared level |

The level a breakout cleared is remembered in `evidence.range_level` for as
long as the match is live. It has to be: the range recomputed on the next bar
would swallow the breakout bar itself and the level would drift upward behind
the price.

* `FORMING` — the bar traded through the range but did not close the declared
  clearance beyond it.
* `MATCHED` — the close cleared the range by more than `min_atr_multiple ×
  atr_14`.
* `ACTIVE` — the close remains beyond the remembered level.
* `INVALID` — the close returned inside that level.

**Over `xau_usd_m5`** with a 19-bar range (the fixture's documented "range high
2410.00 across frames 0..17", `atr_14` 2.40 and clearance 1.20 on frame 18, so
the level to beat is 2411.20 and the close is 2413.50):

```
frame  18       19 .. 23
       MATCHED  ACTIVE ×5
```

The shipped 20-bar package cannot be evaluated until after the breakout bar on
a 24-bar fixture, and honestly publishes `FORMING` throughout rather than
finding a match by shortening the range HELIOS was told to measure.

Tests: `tests/test_atom_range_breakout.py`.

---

### 6.3 `swing_proximity` — proximity to a swing high or low

| | |
|---|---|
| **Reads** | `high`, `low`, `close` |
| **History** | 2 bars minimum, and at least `lookback_bars` |
| **Parameters** | `lookback_bars` (INTEGER, ≥ 2) — bars the swing extremes are measured over; `max_distance` (DECIMAL, ≥ 0) — how near the close must be, in the instrument's own price units |
| **Direction** | `LONG` at the upper extreme, `SHORT` at the lower |
| **Strength** | `1 - distance / max_distance`: 1 at the level itself, 0 at the edge of the declared distance |

**The direction is a statement of location, not intent.** `LONG` means the
close sits at the upper extreme of the declared lookback and `SHORT` the
lower. What that implies about what happens next is a trading judgement, and
HELIOS does not make trading judgements.

If the close is exactly equidistant from both extremes the location is
ambiguous and the atom refuses to pick one: it publishes the ambiguity in the
explanation rather than guessing. The extreme a live match is measured against
is remembered in `evidence.swing_level`, so a later bar making a new extreme
cannot silently move the level the state depends on.

* `FORMING` — the bar reached within `max_distance` of an extreme but its
  close did not.
* `MATCHED` / `ACTIVE` / `WEAKENING` — the close is within `max_distance` of
  the remembered level; weakening as it drifts away.
* `INVALID` — the close moved beyond the level, or outside `max_distance`.

**Over `xau_usd_h1`** with the shipped 24-bar lookback, only the final bar has
enough history: swing high 2413.50, swing low 2392.50, close 2404.00 — 9.50
from the high, within the declared 10.00 → `MATCHED LONG`, strength 0.05.

With a 12-bar lookback the same fixture walks the whole model:

```
frame  11       12 13    14       15       16       17 .. 21     22       23
       MATCHED  ACTIVE   INVALID  DORMANT  MATCHED  WEAKENING ×5  INVALID  DORMANT
```

Tests: `tests/test_atom_swing_proximity.py`.

---

### 6.4 `rejection_wick` — a bar dominated by one wick

| | |
|---|---|
| **Reads** | `open`, `high`, `low`, `close` |
| **History** | 1 bar |
| **Parameters** | `min_wick_ratio` (DECIMAL, 0..1) — wick length as a fraction of the bar's high-to-low range |
| **Direction** | `SHORT` for a dominant upper wick, `LONG` for a dominant lower one |
| **Strength** | the dominant wick's ratio |

The condition is a property of **one bar**, so it matches on that bar and is
invalidated on the next: there is no sense in which yesterday's wick is still
true today. This atom has no forming phase — the geometry either is or is not
there, and `DORMANT -> MATCHED` is legal precisely for conditions like it.

A bar with no range at all is explained rather than divided by.

**Over `xau_usd_m15`** with `min_wick_ratio` 0.6 (frame 5 upper wick 0.846154,
frame 9 lower wick 0.857143, every other frame below the threshold):

```
frame  0 .. 4     5        6        7  8       9        10       11 .. 15
       DORMANT×5  MATCHED  INVALID  DORMANT×2  MATCHED  INVALID  DORMANT×5
```

Tests: `tests/test_atom_rejection_wick.py`.

---

### 6.5 `no_wick_candle` — a no-wick directional candle

| | |
|---|---|
| **Reads** | `open`, `high`, `low`, `close` |
| **History** | 1 bar |
| **Parameters** | `max_wick_ratio` (DECIMAL, 0..1) — largest wick still allowed, as a fraction of the range; `min_body_ratio` (DECIMAL, 0..1) — smallest body that counts |
| **Direction** | `LONG` when the close is above the open, `SHORT` below |
| **Strength** | the body ratio |

A bar that opened at one extreme of its range and closed at the other. A bar
with no body has no direction, so the condition does not hold and the atom
says so instead of picking one. Like §6.4 this is a single-bar property and is
invalidated on the following bar.

* `FORMING` — the body meets its threshold but a wick exceeds
  `max_wick_ratio`.

**Over `xau_usd_m15`** with `max_wick_ratio` 0.05 and `min_body_ratio` 0.90
(frames 3 and 12 are the fixture's documented no-wick bars, body ratio 1 and
wick ratios 0):

```
frame  0 .. 2     3        4        5 .. 11     12       13       14  15
       DORMANT×3  MATCHED  INVALID  DORMANT×7   MATCHED  INVALID  DORMANT×2
```

Tests: `tests/test_atom_no_wick_candle.py`.

---

### 6.6 `momentum_volatility` — momentum at an extreme while volatility expands

| | |
|---|---|
| **Reads** | `close`, `rsi_14`, `atr_14` |
| **History** | 2 bars |
| **Parameters** | `momentum_upper` (DECIMAL, 0..100); `momentum_lower` (DECIMAL, 0..100); `min_volatility_expansion` (DECIMAL, ≥ 1) — smallest ratio of this bar's `atr_14` to the previous bar's that counts as expanding |
| **Direction** | `LONG` at or above `momentum_upper`, `SHORT` at or below `momentum_lower` |
| **Strength** | how far past the declared extreme the reading sits: `(rsi_14 - momentum_upper) / (100 - momentum_upper)` for `LONG`, `(momentum_lower - rsi_14) / momentum_lower` for `SHORT` |

Both readings are HERMES facts. The only arithmetic is the ratio of this
bar's `atr_14` to the previous bar's — strategy-local geometry over two
supplied facts, in the same sense that a wick ratio is geometry over a
supplied candle.

The two legs are deliberately separable, which gives this atom a real forming
phase: **either leg alone is `FORMING`**, and only both together is a match.
Overlapping momentum bounds are refused at bind time — one reading cannot be
both extremes, and HELIOS will not decide which was meant. A previous bar
reporting no volatility at all is explained rather than divided by.

**Over `xau_usd_m5`** with 70.00 / 30.00 and a minimum expansion of 1.50
(`atr_14` is flat at 1.20 through frame 17, doubles to 2.40 at frame 18 while
`rsi_14` reaches 72, then keeps rising by well under 1.5×):

```
frame  1 .. 17     18       19       20       21 .. 23
       DORMANT×17  MATCHED  INVALID  DORMANT  FORMING ×3
```

Tests: `tests/test_atom_momentum_volatility.py`.

---

## 7. Where the tests are

| file | proves |
|---|---|
| `tests/test_atom_support.py` | the replay harness, and that every atom has a reference package |
| `tests/test_atom_framework.py` | binding, parameter checking, freshness, the state model, legal transitions |
| `tests/test_atom_<name>.py` | one atom's hand-checked outcomes over the real fixtures |
| `tests/test_atom_determinism.py` | byte-identical replay, concurrent vs sequential, a separate process |
| `tests/test_isolation.py` | structural independence, shared-fact concurrency, failure containment |

Every expected state sequence in the atom tests was worked out by hand from
`docs/FIXTURES.md` before it was asserted.

## 8. What this work item deliberately does not contain

Composition and chaining, the Docker runtime, and anything on the PID's
non-goal list. Also absent by design and enforced: any reusable indicator
library, any parameter-override path around version immutability, and any
channel through which one strategy could observe another.
