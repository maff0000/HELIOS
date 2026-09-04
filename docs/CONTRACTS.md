# HELIOS contracts

This document is the integration reference for HSA, FALCON and CER. It
describes what HELIOS consumes, what it publishes, how strategy state moves,
how identity works, and the format a strategy package must take.

HELIOS is the deterministic strategy-state engine. It consumes HERMES market
facts, evaluates independent atomic strategies and governed chains, and
publishes normalised state. It is **execution-blind**: it has no knowledge of
trade lifecycle, downstream decisions, account state or broker activity, and
given identical ordered facts and identical definitions it produces identical
state regardless of what any downstream system does. That boundary is enforced
mechanically by `tests/test_execution_blind.py`, which scans every identifier,
literal and fixture value in the tree against
`tests/data/forbidden_execution_vocabulary.txt`.

Everything below is implemented in `helios/contracts/` and `helios/spec/`, and
every claim in this document is covered by a test.

---

## 1. Input contract (HERMES)

HERMES is the market-fact authority. HELIOS consumes indicators as **given
facts** and never recomputes them: there is deliberately no reusable indicator
library in this codebase. Strategy-local derived logic — "is this candle's wick
more than 60% of its range" — belongs to an individual strategy; "compute
ema_200" is HERMES's job.

### 1.1 Timeframe

`Timeframe` (`helios/contracts/timeframe.py`) supports `M1, M5, M15, H1, H4,
D1` and knows each one's duration. Both the HELIOS code (`H4`) and the HERMES
code (`4h`) parse; the HELIOS code is always what is published.

The duration is the only input to freshness limits, so one rule scales across
every timeframe without a per-timeframe table.

> **There is no global mapping from timeframe to semantic role.** The PID's
> GOLD template (`CONTEXT`→4H, `LOCATION`→1H, `CONFIRMATION`→15M,
> `TRIGGER`→5M) is *per-strategy configuration*, declared in a strategy
> package's `inputs` block. A different strategy may put `CONTEXT` on D1 and
> HELIOS neither knows nor cares. `SemanticRole` is an open token vocabulary so
> strategies may use fewer or more stages.

### 1.2 Candle and indicators

`Candle` carries exactly the HERMES candle fields:

| field | type | notes |
|---|---|---|
| `open`, `high`, `low`, `close` | decimal | must be positive; `high >= max(open, close)`, `low <= min(open, close)` |
| `volume` | decimal | must not be negative |
| `complete` | boolean | whether HERMES considers the bar closed |
| `source` | token | fact source as HERMES reports it |

`IndicatorSet` carries the HERMES indicator/context fields, **all optional**:
`rsi_14`, `ema_9`, `ema_21`, `ema_50`, `ema_200`, `atr_14`, `regime`,
`session`. Not every frame carries every indicator — `ema_200` does not exist
until 200 bars of history do. An absent indicator is `None` and is **never**
silently replaced with zero. A strategy that needs one asks via
`frame.fact("ema_200")` or `indicators.require(...)`, which raise
`MissingFactError` naming the field, instrument, timeframe and timestamp.

`regime` and `session` are validated for *shape* but their value sets are open:
HERMES owns that vocabulary, so an unrecognised-but-well-formed regime is a
fact HELIOS does not understand, not malformed input. Observed regimes include
`BULL_TREND`, `BEAR_TREND`, `LOW_VOLATILITY`, `TRANSITION`.

### 1.3 Frame, provenance and window

`MarketFactFrame` binds `instrument + timeframe + timestamp_utc (bar open) +
candle + indicators + provenance`. `Provenance` carries `source`,
`schema_version`, `observed_at_utc` (when the authority produced the fact) and
`ingested_at_utc` (when HELIOS received it).

`MarketFactWindow` is an ordered, immutable window per `(instrument,
timeframe)` giving strategies deterministic lookback. Frames must be strictly
ascending; gaps are fine (markets close), duplicates and out-of-order frames
are malformed input. `window.lookback(n)` raises rather than quietly returning
fewer frames than a strategy declared it needs.

### 1.4 Two strictnesses worth knowing about

**Binary floats are rejected at the boundary.** Prices and indicator values are
`Decimal`, fed from exact decimal text. `0.1 + 0.2 != 0.3` in binary floating
point, and HELIOS promises identical output for identical inputs. Supply a
string or a `Decimal`; a `float` raises `ContractViolationError`.

**Unknown fields are rejected.** Every contract model is `extra="forbid"`. If
HERMES adds a field, that is a deliberate HELIOS schema-version change, never a
silent pass-through.

### 1.5 Freshness and validity

`FreshnessPolicy` (built from configuration, never from source defaults)
decides whether a fact may be evaluated against:

```
max_age(timeframe) = timeframe.duration * max_age_multiplier + grace
```

measured from the bar's **close** instant, with optional per-timeframe absolute
overrides. `assess_frame()` reports a `FreshnessVerdict`;
`require_fresh_frame()` raises `StaleFactError`. A missing window raises
`MissingFactError`. An incomplete bar is refused unless
`allow_incomplete_frames` is configured true. Every verdict is published in the
output envelope's `inputs`, so a consumer can see exactly how fresh the facts
behind a state were.

Nothing is ever silently defaulted, silently zeroed or silently substituted.

---

## 2. State model

Seven states, adopted **verbatim** from the PID. FORGE is permitted to refine
the naming; no refinement was needed, and keeping the PID's own vocabulary
keeps HSA, FALCON and this engine speaking one language. What the PID did not
fix — the legal transitions — is defined here and tested.

| state | meaning |
|---|---|
| `DORMANT` | evaluated; no part of the condition currently holds |
| `FORMING` | some declared precondition holds, the full condition does not yet |
| `MATCHED` | the full condition became true on this evaluation — the match edge |
| `ACTIVE` | the match was observed before and still holds |
| `WEAKENING` | the match still holds but a declared strength measure is degrading |
| `INVALID` | a declared invalidation condition fired; the match is void |
| `EXPIRED` | the match aged past its validity window without being invalidated |

`INVALID` and `EXPIRED` are distinct on purpose: something broke, versus time
ran out.

### 2.1 Legal transitions

```
DORMANT   -> DORMANT, FORMING, MATCHED
FORMING   -> FORMING, MATCHED, DORMANT, INVALID, EXPIRED
MATCHED   -> MATCHED, ACTIVE, WEAKENING, INVALID, EXPIRED
ACTIVE    -> ACTIVE, WEAKENING, INVALID, EXPIRED
WEAKENING -> WEAKENING, ACTIVE, INVALID, EXPIRED
INVALID   -> INVALID, DORMANT
EXPIRED   -> EXPIRED, DORMANT
```

Rationale for the non-obvious edges:

* **Self-transitions are legal everywhere.** HELIOS re-evaluates continuously
  and republishes an unchanged state; that is not an error.
* **`DORMANT -> MATCHED` is legal.** An atomic such as a moving-average cross
  matches outright with no forming phase.
* **A live state may not fall back to `DORMANT` or `FORMING`.** Once matched,
  the occurrence must resolve explicitly through `INVALID` or `EXPIRED`, which
  keeps the lifecycle auditable. Resolved states rearm only via `DORMANT`.
* **`WEAKENING -> ACTIVE` is legal, `WEAKENING -> MATCHED` is not.** Recovery
  is expected; re-firing the match edge would misreport a match that already
  happened.

Anything outside the table raises `IllegalStateTransitionError`. Every
transition carries a non-empty `reason`.

### 2.2 Lifecycle timestamps

`advance_lifecycle()` derives the published instants deterministically, the
same way for atomic strategies and chains:

* `last_evaluated_at_utc` — always the evaluation instant.
* `last_matched_at_utc` — updated on every live evaluation (`MATCHED`,
  `ACTIVE`, `WEAKENING`).
* `first_matched_at_utc` — set once per occurrence, on the first live
  evaluation.
* `active_since_utc` — set when the strategy becomes live, and **preserved
  across an `ACTIVE -> WEAKENING -> ACTIVE` excursion**, because that is one
  continuous activation. Cleared when the strategy stops being live.
* Returning to `DORMANT` clears the occurrence. `INVALID`/`EXPIRED` retain the
  history so consumers can see what just ended and when.

### 2.3 Direction

`LONG`, `SHORT`, `NEUTRAL`, `NONE`. `NEUTRAL` and `NONE` are distinct:
`NEUTRAL` means a direction-aware strategy currently finds no bias; `NONE`
means the strategy is not directional at all. Chains relating component
directions need to tell those apart.

---

## 3. Output contract (FALCON)

**One envelope** is published for atomic strategies and chains alike, so FALCON
never reverse-engineers a strategy-specific format. Schema version:
`helios.strategy_state/1.0.0`.

| field | notes |
|---|---|
| `schema_version` | consumers refusing an unknown value is correct behaviour |
| `kind` | `ATOMIC` or `CHAIN` |
| `strategy_id`, `strategy_version` | identity of the publishing unit — always present |
| `chain_id`, `chain_version` | `null` for atomics; for chains, equal to the two fields above |
| `instrument` | |
| `timeframe`, `semantic_role` | an atomic states at least one; a chain may state neither |
| `state` | see §2 |
| `direction` | see §2.3 |
| `strength` | optional decimal in `0..1`, where the strategy defines one |
| `evidence` | flat immutable map of strategy-declared scalars |
| `explanation` | why a chain matched or did not |
| `first_matched_at_utc`, `last_matched_at_utc`, `active_since_utc`, `last_evaluated_at_utc` | see §2.2 |
| `validity` | `valid_from_utc`, `valid_until_utc` (`null` = open-ended), `reason` |
| `components` | per-component provenance for chains; empty for atomics |
| `inputs` | freshness/source metadata for every fact behind this state |

### 3.1 Why a chain's identity appears twice

For `kind = CHAIN`, the chain's identity is published in **both**
`strategy_id`/`strategy_version` and `chain_id`/`chain_version`. The first pair
lets any consumer read "who published this" with no branching on `kind`; the
second is the field name the PID requires and that chain-aware consumers key
on. The contract validates that the two agree, so the duplication cannot drift.

### 3.2 Component provenance

Each chain component publishes `strategy_id`, `strategy_version`, `state`,
`direction`, `timeframe`, `semantic_role`, `sequence_index`, `matched`,
`last_evaluated_at_utc` and a `contribution` note. `matched` is validated
against `state`, so it cannot contradict it.

v1 components reference **atomic** strategies. Chain-of-chain recursion is not
expressible: `ChainComponent` has no chain field, per the PID's requirement for
explicit architecture authority before introducing it.

### 3.3 Deterministic serialisation

`to_canonical_json()` produces byte-stable JSON:

* object keys sorted, no insignificant whitespace;
* instants as fixed-width `YYYY-MM-DDTHH:MM:SS.ffffffZ`;
* decimals as normalised exact strings (`2400.00` → `"2400"`), never floats;
* `null` preserved rather than omitted, so an absent fact is visibly absent;
* `NaN`/`Infinity` refused rather than emitted as invalid JSON.

`from_canonical_json()` round-trips exactly and reads every JSON number as a
`Decimal`.

### 3.4 Immutability

Envelopes are frozen, `evidence` is an immutable mapping and `components` and
`inputs` are tuples. A published envelope cannot be edited by whoever holds it.

---

## 4. Identity (CER)

HELIOS **originates** two canonical identity fields and carries them through
everything it publishes:

* `strategy_id` — `^[a-z][a-z0-9_]{2,63}$`, e.g. `golden_cross`
* `strategy_version` — strict `major.minor.patch`; no leading zeros, no
  pre-release suffixes

`ChainId` is a structurally distinct type from `StrategyId`, so a chain
identifier cannot be silently used where an atomic strategy is required.
`StrategyIdentity` renders canonically as `golden_cross@1.0.0`.

### 4.1 Immutability of a promoted version

A promoted `(strategy_id, strategy_version)` pair is immutable. **A logic
change means a NEW version, never an in-place tweak** — otherwise CER's
recorded evidence would silently stop describing the strategy that produced it.
`assert_version_immutable()` expresses this: it raises if a changed definition
reuses a promoted version, and equally if an unchanged definition bumps one.

### 4.2 What HELIOS does not own

`experiment_id`, `run_id`, `evidence_id` and `artifact_id` are **CER's**. HELIOS
neither mints nor stores them, and a guard test asserts those names never
appear in the output envelope. HELIOS publishes runtime strategy state; CER
owns durable empirical evidence. `evidence` in the envelope is per-evaluation
explanation, not a store.

---

## 5. Strategy package format (HSA)

HSA emits declarative packages; HELIOS loads them. Schema version
`helios.strategy_package/1.0.0`; JSON or YAML; JSON Schema is checked in at
`docs/schema/strategy_package.schema.json` and a test fails if it drifts from
the model.

```yaml
schema_version: helios.strategy_package/1.0.0
kind: ATOMIC                       # or CHAIN
identity:
  strategy_id: golden_cross
  strategy_version: 1.0.0
metadata:
  title: Golden / death cross
  description: ...
  authored_by: HSA
  authored_at_utc: 2026-01-02T09:00:00Z
inputs:                            # the ONLY role -> timeframe binding
  - role: CONTEXT
    timeframe: H4
    lookback: 2
    required_fields: [close, ema_50, ema_200]
    max_age_seconds: 21600         # optional freshness override
parameters:
  min_separation:
    type: DECIMAL                  # INTEGER | DECIMAL | BOOLEAN | STRING
    value: 0.25
    minimum: 0
    maximum: 100
direction:
  mode: DIRECTIONAL                # or NON_DIRECTIONAL
  resolution: STRATEGY_LOCAL       # chains use FROM_COMPONENTS
timing:
  evaluate_on: CLOSED_FRAME        # or EVERY_FRAME
persistence:
  min_matched_frames: 1
  weakening_enabled: true
expiry:
  mode: FRAMES                     # NEVER | FRAMES | DURATION
  frames: 6
chain:                             # CHAIN packages only
  primitive: CONTEXT_TRIGGER       # ALL | ANY | SEQUENCE | CONTEXT_TRIGGER
  explanation_required: true
  ordering_window_seconds: 28800   # SEQUENCE only
  components:
    - strategy_id: golden_cross
      strategy_version: 1.0.0
      role: CONTEXT
      sequence_index: 0            # SEQUENCE only
      direction_relationship: SAME # SAME | OPPOSITE | ANY
      required_states: [MATCHED, ACTIVE, WEAKENING]
```

### 5.1 HELIOS never invents trading logic to fill a gap

Every ambiguity is a loud `StrategySpecError` naming the offending file, not a
default. The rules that are enforced:

* an unrecognised `schema_version` is refused, not guessed at;
* an unknown key is refused — a typo must never become a silent no-op;
* a `required_fields` entry HERMES does not publish is refused; HELIOS will not
  invent a fact;
* a parameter value outside its own declared `minimum`/`maximum` is refused;
* two inputs binding the same semantic role are refused — the role-to-timeframe
  mapping would be ambiguous;
* an `ATOMIC` package declaring a chain, or a `CHAIN` package without one, is
  refused;
* a `SEQUENCE` chain must declare `ordering_window_seconds` — "in order, within
  what period?" has no safe default — and its `sequence_index` values must be
  unique and contiguous from 0;
* only a `SEQUENCE` may declare ordering at all; an `ALL`/`ANY` chain that also
  states an order is making two contradictory claims and is refused;
* a `CONTEXT_TRIGGER` chain must name exactly one `CONTEXT` and exactly one
  `TRIGGER` component;
* an expiry mode without its value, or with both a frame count and a duration,
  is refused;
* a chain must combine at least two distinct components;
* duplicate identities across packages are refused — two definitions claiming
  one promoted version would make published state non-reproducible.

Numeric scalars are read as exact `Decimal`s from both JSON and YAML, so a
package's parameters mean exactly what HSA wrote.

---

## 6. Evaluation interface

```python
class StrategyEvaluator(Protocol):
    @property
    def identity(self) -> StrategyIdentity: ...
    def required_inputs(self) -> tuple[RequiredInput, ...]: ...
    def evaluate(self, context: EvaluationContext) -> StrategyStateEnvelope: ...
```

`EvaluationContext` carries exactly `instrument`, `evaluated_at_utc`, `windows`
(keyed by semantic role), `parameters`, `freshness_policy` and `previous` (the
strategy's own last envelope). That is the strategy's entire world — there is
no field through which downstream activity could reach it, which is what makes
execution blindness structural rather than a convention. `window_for()` and
`parameter()` fail loudly on anything undeclared.

---

## 7. Configuration and logging

**No configuration values live in source.** Everything comes from `HELIOS_*`
environment variables or a TOML file named by `HELIOS_CONFIG_FILE`; the
environment wins, so one container image runs everywhere and only the injected
environment differs. `load_config()` validates at startup and reports **every**
problem at once. See `config/helios.example.toml`.

Logs are one JSON object per line with sorted keys and UTC timestamps ending in
`Z`. `HeliosError` context is emitted as structured fields, so a failure is
machine-queryable. Local time appears nowhere.

---

## 8. Error taxonomy

| error | raised when |
|---|---|
| `ContractViolationError` | malformed market fact, frame, window or value token |
| `MissingFactError` | a required fact, indicator, window or parameter is absent |
| `StaleFactError` | a fact exists but is older than its configured limit |
| `IllegalStateTransitionError` | a proposed state transition is not in the legal table |
| `IdentityError` | malformed identity/version, or a promotion-immutability breach |
| `StrategySpecError` | a strategy package is malformed, ambiguous or incomplete |
| `ConfigurationError` | required runtime configuration is missing or invalid |
| `SerialisationError` | a value has no deterministic representation |

All inherit `HeliosError` and carry a structured `context` mapping.

---

## 9. What this work item deliberately does not contain

The atomic strategies themselves, the composition engine and the Docker runtime
are separate work items. WI-1 defines the vocabulary they consume. Also absent
by design, and enforced: NEO decision logic, TRON execution, account risk,
broker integration, a backtesting engine, strategy auto-tuning, a dashboard, an
evidence store, and any reusable indicator library.
