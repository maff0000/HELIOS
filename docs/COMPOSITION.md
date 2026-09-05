# HELIOS composition — chains over atomic strategy state

This document is the reference for the HELIOS composition layer: what each
chain primitive means exactly, how direction, ordering, persistence and expiry
are decided, and how a chain explains itself when it does **not** match.

It describes `helios/composition/`. Every claim below is covered by a test in
`tests/test_chain_*.py`.

---

## 1. What a chain is, and what it is allowed to see

A chain composes the **normalised output envelopes** of atomic strategies. Its
entire input is:

```python
ChainEngine(package).evaluate(
    instrument=...,             # the subject
    evaluated_at_utc=...,       # a UTC instant
    components=[...],           # StrategyStateEnvelope values, kind = ATOMIC
    previous=...,               # this chain's own last envelope, or None
)
```

That is the whole world. The composition layer imports no concrete atomic
strategy, references none by module and cannot reach one. This is the PID's
separation made structural rather than conventional: *atomic strategies do not
know other strategies exist*, and the layer that combines them knows only what
they publish.

It also means a chain is testable, and was built, entirely against synthetic
envelopes constructed from the contract layer. A chain test that needed a real
atomic strategy would be evidence the two are coupled.

**One evaluation is a pure function** of `(package, component envelopes,
evaluation instant, previous chain envelope)`. The engine holds no state
between calls — including the persistence count, which is published in the
envelope's `evidence` so the next evaluation can resume from the published
record alone. Replaying the same ordered inputs reproduces every envelope byte
for byte.

### 1.1 v1 boundary: no chain of chains

A component must be an `ATOMIC` envelope. Supplying a `CHAIN` envelope as a
component raises `StrategySpecError`. The package format cannot express it
either — `ChainComponent` has no chain field. Recursive chain-of-chain
composition requires explicit architecture authority per the PID, and is
refused at both levels rather than quietly half-supported.

### 1.2 No timeframe lives here

Nothing in `helios/composition/` names a timeframe. A chain learns which
timeframe fills which semantic role from its **own package's `inputs` block**,
and nowhere else. `tests/test_chain_multi_timeframe.py` asserts this
mechanically: no `Timeframe` code or HERMES code appears as an identifier or
string literal anywhere in the engine's source.

The engine names exactly two role tokens, `CONTEXT` and `TRIGGER`, because
those are structural to the `CONTEXT_TRIGGER` primitive itself — the spec
requires exactly one of each. `LOCATION` and `CONFIRMATION` are stages of the
PID's GOLD *template*, are data as far as HELIOS is concerned, and are asserted
to appear nowhere in the engine.

---

## 2. Canonical component order

Each primitive reasons about its components in a fixed order. Nothing else —
in particular, no timeframe — influences it.

| primitive | canonical order |
|---|---|
| `SEQUENCE` | ascending `sequence_index` (the spec guarantees unique and contiguous from 0) |
| `CONTEXT_TRIGGER` | the `CONTEXT` component, then any intermediate components in declared order, then the `TRIGGER` component |
| `ALL`, `ANY` | the order the package declared |

Canonical order fixes which component is the direction anchor, which failure is
reported as "the first" one, and the order in which provenance and the
explanation are rendered — so all four are stable across evaluations.

---

## 3. Does one component hold?

Before any primitive runs, each declared component is assessed on its own
terms, in this fixed order. Each step is a stated rule; none of them can be
reached by a crash, a default or a silent pass.

| # | check | reason code when it fails |
|---|---|---|
| 1 | a state was supplied for this component at all | `NOT_SUPPLIED` |
| 2 | it was supplied for the **exact version** the chain declares | `VERSION_MISMATCH` |
| 3 | every market fact behind that state was fresh | `STALE_COMPONENT_INPUT` |
| 4 | the component's own declared validity had not lapsed | `COMPONENT_VALIDITY_LAPSED` |
| 5 | the state is one of the chain's `required_states` | `STATE_NOT_REQUIRED`, or `COMPONENT_INVALIDATED` / `COMPONENT_AGED_OUT` |

Notes on the non-obvious ones:

* **Version is part of identity.** A chain must be able to say precisely which
  component versions produced its state, so an envelope for a different version
  is a *different component*, not a near-enough substitute. The failure names
  both the declared version and the version(s) actually supplied.
* **A stale fact beats a confident state.** A component that claims `MATCHED`
  on a fact HELIOS considers stale does not count, whatever it claims. Staleness
  is checked before state for exactly that reason.
* **Component persistence is the component's own business.** "How long does a
  component's match keep counting?" is answered by the component: its state,
  and its published `validity.valid_until_utc`. The chain honours that window
  and does not invent one of its own. `SEQUENCE` and `CONTEXT_TRIGGER` then add
  their own temporal constraints on top (§5.3, §5.4).
* **`INVALID` and `EXPIRED` are reported distinctly** from an ordinary state
  mismatch — something broke, versus time ran out — and the component's own
  `validity.reason` is quoted into the chain's explanation when it has one.
* **The chain declares what satisfies it.** If a package declares
  `required_states: [INVALID]`, then `INVALID` satisfies that component. HELIOS
  does not second-guess an HSA-approved specification.

Only after all five pass is direction checked (§4), and only then does the
primitive run (§5).

---

## 4. Direction

A chain package declares `resolution: FROM_COMPONENTS`, so a chain has no
direction of its own — it adopts one. "Which direction is the chain?" and
"which components count?" would otherwise depend on each other circularly.
HELIOS breaks that loop in one stated order:

1. resolve the chain's direction from the components that hold on their own
   terms (§3);
2. then check every component's direction against that result.

### 4.1 Resolving the chain's direction

* A `NON_DIRECTIONAL` chain publishes `NONE`. Direction is not applicable to
  it — a different statement from having no bias.
* A `DIRECTIONAL` chain adopts the direction of its **anchor**: the first
  component, in canonical order, that holds, declares
  `direction_relationship: SAME`, and publishes `LONG` or `SHORT`.
* A `DIRECTIONAL` chain with no directional anchor publishes `NEUTRAL`: it is
  direction-aware and currently has no resolvable bias.

**Only a `SAME` component can be the anchor.** A component declared `OPPOSITE`
states the inverse of the chain's bias by definition, so anchoring on it would
invert the chain's meaning; one declared `ANY` states nothing about the chain's
bias, so anchoring on it would invent one. A `DIRECTIONAL` chain that declares
no `SAME` component at all is refused at engine construction — there is nothing
for it to adopt a direction from, and HELIOS does not guess.

### 4.2 Checking each component

| relationship | satisfied when |
|---|---|
| `SAME` | the component's direction is *identical* to the chain's |
| `OPPOSITE` | both are directional and opposed |
| `ANY` | always — the component participates on state alone |

`SAME` is identity rather than "both LONG or both SHORT", so a `NEUTRAL` chain
is satisfied only by `NEUTRAL` components and a `NONE` chain only by
non-directional ones. A `NEUTRAL` component never counts as agreeing with a
`LONG` chain: `NEUTRAL` means *no bias*, not *any bias*.

`NEUTRAL` and `NONE` can never satisfy `OPPOSITE` — neither can oppose
anything.

### 4.3 A live chain's direction is pinned

While a chain is live (`MATCHED` / `ACTIVE` / `WEAKENING`), the direction it
matched in is **pinned**, and components are checked against that pinned value
rather than a freshly resolved one. If the anchor reverses, the chain is voided
(`INVALID`) rather than silently re-published in the other direction. "Which
direction did this chain match in" is a durable fact about the occurrence.

### 4.4 The package-level relationship

`direction.component_relationship` states the chain's overall directional
doctrine; each component states its own relationship to the chain. Left
unchecked the two could disagree, and HELIOS would have to pick one. So the
engine checks them at construction:

* `SAME` overall — no component may declare `OPPOSITE`;
* `OPPOSITE` overall — at least one component must declare `OPPOSITE`;
* `ANY` overall — no constraint.

A contradiction raises `StrategySpecError` naming the package.

---

## 5. The four primitives

### 5.1 `ALL`

Satisfied when **every** declared component holds. Nothing else is considered:
no ordering (the spec refuses an `ALL` chain that declares one), no timing
relationship between components.

### 5.2 `ANY`

Satisfied when **at least one** declared component holds. A component that does
not hold is not a chain failure — it contributes nothing. Its own reason is
still published, so an `ANY` chain still explains every component, and its
`strength` (§7) makes "two of three" visibly weaker than "three of three".

### 5.3 `SEQUENCE`

Satisfied when every component holds **and** they matched in the declared
order, within the declared `ordering_window_seconds`.

**Which instant establishes order.** Each component's
`first_matched_at_utc` — when *this occurrence* of its condition began. Not
`last_matched_at_utc`, which advances on every live evaluation: a component
that matched first but is still `ACTIVE` would otherwise appear to have matched
last.

**Ties are in order.** Two consecutive components sharing an identical match
instant satisfy the ordering. HELIOS evaluates on frame boundaries and a 4H
close is also a 5M close, so a genuinely simultaneous match across two
timeframes is ordinary, not a violation; requiring a strict increase would make
such a chain unmatchable for reasons that have nothing to do with the strategy.
Only a later component matching *strictly earlier* than the one declared before
it breaks the order — reported on that component as `OUT_OF_DECLARED_ORDER`,
naming both instants. **The engine never reorders components to make a sequence
fit.**

**The window** is measured between the earliest and the latest match instant
and is **inclusive**: a span exactly equal to `ordering_window_seconds` is
inside it. A lapsed window is a *chain-level* reason, not a per-component one —
each component still holds on its own terms; it is their combination that does
not fit. Marking them individually unsatisfied would misreport the chain as
having nothing at all.

A component satisfying on a non-live state (a package may declare
`required_states: [FORMING]`) publishes no match instant, so its place in the
order cannot be established: `NO_MATCH_INSTANT`.

### 5.4 `CONTEXT_TRIGGER`

Satisfied when the context is established and still valid at the moment the
trigger matches. The spec guarantees exactly one `CONTEXT` and one `TRIGGER`
component.

1. every component holds — including any intermediate ones. Declaring a
   component and then ignoring it would be a silent pass;
2. the context's match instant is **not later than** the trigger's. A context
   established after its trigger did not frame that trigger
   (`CONTEXT_NOT_ESTABLISHED_FIRST`). Simultaneous instants are accepted, for
   the same reason ties are accepted in a `SEQUENCE`;
3. if the context publishes its own `valid_until_utc`, the trigger's match
   instant is not after it (`CONTEXT_VALIDITY_LAPSED`). An expired context
   frames nothing;
4. the context is still holding at this evaluation — already covered by its own
   state and validity checks in §3.

**"Higher timeframe" is checked at definition time, from the package's own
bindings.** If the package binds both roles to timeframes and the `CONTEXT`
timeframe is *finer* than the `TRIGGER` timeframe, the engine refuses the chain
with `StrategySpecError`. A context cannot frame something slower than itself.
Equal timeframes are permitted. No timeframe is hard-coded: a chain may put its
context on D1, H4 or M15 as it pleases.

---

## 6. Chain state

The composed verdict ("does it hold right now") and the published state ("what
does the chain therefore say") are deliberately separate, so each can be tested
alone. Given a carry-in state — the previous envelope's state, or `DORMANT` on
a first evaluation — the rules are applied in this order:

**A resolved occurrence latches.** From `INVALID` or `EXPIRED`, the same state
is republished while the composed condition still holds, and the chain rearms
to `DORMANT` once the condition clears. A resolved chain therefore cannot
re-fire on the same evidence that resolved it. This also keeps the published
sequence inside the state model's transition table, which allows
`INVALID -> DORMANT` but not `INVALID -> MATCHED`.

**A live chain checks time before condition.** If its declared validity window
has passed, it publishes `EXPIRED` — time ran out — even when the condition
also stopped holding. Nothing broke, so `INVALID` would misreport it.

**A live chain whose condition stops holding is `INVALID`.** The state model
forbids falling back to `DORMANT`/`FORMING`: an occurrence that started must
resolve explicitly, so the lifecycle stays auditable. The explanation names the
first component that failed, and why.

**A live chain that still holds is `ACTIVE`, or `WEAKENING`** — see §7.

**A non-live chain matches only after `persistence.min_matched_frames`
consecutive satisfied evaluations.** That is chain-level persistence. Below the
threshold it publishes `FORMING` and states how far along it is ("held on 1 of
the 2 consecutive evaluations the package requires"). A broken run resets the
count to zero.

**A partially satisfied chain is `FORMING`**; one with nothing holding at all
is `DORMANT`.

Every published transition is validated against
`helios.contracts.state.LEGAL_TRANSITIONS` before the envelope is built, so the
engine cannot emit a sequence the documented state model forbids.

### 6.1 Lifecycle timestamps

Derived by the shared `advance_lifecycle()`, identically to atomic strategies.
`active_since_utc` survives an `ACTIVE -> WEAKENING -> ACTIVE` excursion —
that is one continuous activation. Returning to `DORMANT` clears the
occurrence; `INVALID`/`EXPIRED` retain the history so a consumer can see what
just ended and when.

---

## 7. Strength and weakening

A chain's `strength` is the share of declared components that hold, published
as an exact `Decimal` quantized to four places (`3/4` → `0.7500`). It is a
`Decimal` throughout; binary floats are refused at the contract boundary.

`WEAKENING` is published only when `persistence.weakening_enabled` is true, and
only for a chain that is still live and still satisfied. Two declared measures,
both derived from published component state rather than invented:

1. a contributing component publishes `WEAKENING` itself;
2. the chain's strength fell since the previous evaluation. This is what makes
   `ANY` meaningful: two of three components holding is a weaker chain than
   three of three.

`WEAKENING` may recover to `ACTIVE`. It never re-fires the `MATCHED` edge — the
match already happened.

---

## 8. Expiry

`expiry` decides how long a matched chain occurrence stays valid. It is
published as `validity.valid_from_utc` / `validity.valid_until_utc` on every
live envelope.

| mode | horizon |
|---|---|
| `NEVER` | open-ended; `valid_until_utc` is `null` |
| `DURATION` | exactly `duration_seconds` from `first_matched_at_utc` |
| `FRAMES` | `frames` × the duration of the **finest timeframe the package binds** |

`FRAMES` needs a decision the spec does not make for chains, because a chain
binds several timeframes. HELIOS counts frames of the finest one, because that
is the resolution at which a chain concludes — a `CONTEXT_TRIGGER` chain's
trigger is by definition on its finest timeframe, so "6 frames" for such a
chain means six trigger frames. The timeframe comes from the package's own
bindings; there is no default. A `FRAMES` chain that binds **no** timeframe at
all is refused at construction rather than guessed at: "frames of what?" has no
safe answer.

When the horizon passes, the next evaluation of a live chain publishes
`EXPIRED`, and the validity window it expired against is preserved on that
envelope rather than being recomputed.

`INVALID` publishes `valid_from_utc` = the occurrence's first match,
`valid_until_utc` = the instant it became void, and a `reason`. A latched
resolved state republishes the validity it resolved with, unchanged, so the
record of *when* it ended cannot drift.

---

## 9. How a non-match is explained

The PID requires an explicit explanation of why a chain matched **or did not
match**. A non-match is therefore a structured result, never an absent one:
every evaluation produces one outcome per declared component, including for a
component whose state was never supplied.

Three things are published, all rendered from that same set of outcomes so they
cannot disagree:

**`explanation`** — one deterministic prose sequence:

> `SEQUENCE chain gold_staged_sequence@1.0.0 on XAU_USD did not match LONG.
> rejection_wick@1.0.0 (CONFIRMATION, M15, index 2) is not satisfied —
> STATE_NOT_REQUIRED: state DORMANT is not one of the required states MATCHED.
> Still holding: golden_cross@1.0.0 (CONTEXT, H4, index 0) ACTIVE LONG at
> 2026-01-05T06:00:00.000000Z; …. SEQUENCE requires all 4 components to hold in
> the declared order. 3 of 4 components satisfied. published state FORMING: 3 of
> 4 components hold; the composed condition does not.`

Note that a non-match names the components that **did** hold as well as the
ones that did not, so a reader sees the whole picture rather than only the
culprit.

**`components`** — per-component provenance carrying `strategy_id`,
`strategy_version`, `state`, `direction`, `timeframe`, `semantic_role`,
`sequence_index`, `matched`, `last_evaluated_at_utc`, and a `contribution` note
of the form `REASON: detail`.

**`evidence`** — flat scalars for machine consumers: `primitive`,
`components_declared`, `components_satisfied`, `satisfied_evaluations`,
`min_matched_frames`, `resolved_direction`, `expiry_mode`, `validity_seconds`,
`ordering_window_seconds`, and — when the chain did not match —
`first_unsatisfied_component` and `first_unsatisfied_reason`.

### 9.1 Publishing a component that was never supplied

The output contract has no `UNKNOWN` state. A component whose state was not
supplied, or was supplied for a version the chain does not declare, is
published with `state = INVALID` and `matched = false`: from the chain's
viewpoint that component cannot be shown to hold, so the match is void as far
as it is concerned. Publishing `DORMANT` instead would claim HELIOS observed
the component saying so. `contribution` always names the exact rule
(`NOT_SUPPLIED: …`, `VERSION_MISMATCH: …`), so the distinction is never lost.

`matched` carries the contract's own meaning — whether the component's own
condition holds, validated against `state`. Whether the component satisfied
*this chain* is a different question, answered by `contribution`.

### 9.2 Reason vocabulary

`SATISFIED`, `NOT_SUPPLIED`, `VERSION_MISMATCH`, `STALE_COMPONENT_INPUT`,
`COMPONENT_VALIDITY_LAPSED`, `COMPONENT_INVALIDATED`, `COMPONENT_AGED_OUT`,
`STATE_NOT_REQUIRED`, `DIRECTION_INCOMPATIBLE`, `NO_MATCH_INSTANT`,
`OUT_OF_DECLARED_ORDER`, `CONTEXT_NOT_ESTABLISHED_FIRST`,
`CONTEXT_VALIDITY_LAPSED`.

---

## 10. What raises, and what does not

**Market conditions never raise.** A missing component, a stale one, an
invalidated one, an aged-out one, a wrong-version one, an out-of-order sequence
— all resolve by explicit rule into a well-defined chain state with an
explanation. Never a crash, never a silent pass.

**Wiring and definition faults raise loudly**, because a plausible-looking
result would be worse than a failure:

| raised | when |
|---|---|
| `StrategySpecError` | an `ATOMIC` package handed to the chain engine; a `CHAIN` envelope supplied as a component; a `CONTEXT` bound finer than its `TRIGGER`; a `FRAMES` expiry with no bound timeframe; a `DIRECTIONAL` chain with no `SAME` anchor; a package-level direction doctrine contradicting its components |
| `ContractViolationError` | a component state for a different instrument; two states for one component identity; a non-envelope component; a naive evaluation instant; a chain handed another unit's envelope to resume from |

Component states the chain does not declare are **ignored**, not refused: a
runtime may reasonably hand the engine every atomic state it holds.

### 10.1 Isolation

A chain resumes only from **its own** previous envelope — same kind, same
identity, same version, same instrument — so one strategy's state cannot be
inherited by another. Every envelope is frozen and every mapping on it is
immutable, so evaluating a chain cannot mutate the component states it was
handed, and two chains sharing components resolve independently.

---

## 11. Execution blindness

Nothing in the composition layer can observe downstream activity. Its entire
input is market-fact-derived strategy state plus its own definition; there is
no field, parameter or channel through which trade, position, broker or account
information could reach it. `tests/test_execution_blind.py` enforces the
boundary mechanically across this package like every other.

---

## 12. What this layer deliberately does not contain

The atomic strategies themselves (`docs/ATOMS.md`), any indicator computation,
chain-of-chain recursion, the running service (`docs/RUNTIME.md`), NEO/TRON
logic, account risk, backtesting, auto-tuning, dashboards, and an evidence
store.
