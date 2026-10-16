# Reusable atoms and strategy package model

Added scope: Central Architecture directive 16 October 2026 (HELIOS Strategy Development Standard and Common Strategy Language), relayed by ROGUE. All citations are to HELIOS `7f7193d55475e40583892b94dd461b1b64d6f1ef` unless stated. Labels: PROVEN / PARTIAL / UNVERIFIED / MISSING. The re-engineered HELIOS is preserved; this section authorises no replacement and freezes no contract. `GOLD-TREND-PO3-001` (family "Trend-Pullback-PO3") is a **design candidate only**: not selected, implemented, qualified or promoted.

## 1. Inventory

### 1.1 Atoms (PROVEN: `helios/strategies/catalogue.py` ATOM_TYPES; each file under `helios/strategies/atoms/`)

| Atom (`ATOM_NAME`) | Facts it requires | Tunable parameters | Edge condition | Directional | Note |
|---|---|---|---|---|---|
| `golden_cross` | close, `ema_50`, `ema_200` | `min_separation` | yes | yes | EMA cross with a margin; not a trend-state atom |
| `momentum_volatility` | close, `rsi_14`, `atr_14` | `momentum_upper`, `momentum_lower`, `min_volatility_expansion` | no | yes | compares ATR to the previous bar's |
| `no_wick_candle` | open, high, low, close | `max_wick_ratio`, `min_body_ratio` | no | yes | |
| `range_breakout` | high, low, close, `atr_14` | `lookback_bars`, `min_atr_multiple` | no | yes | prior-range break by an ATR multiple |
| `rejection_wick` | open, high, low, close | `min_wick_ratio` | no | yes | |
| `swing_proximity` | high, low, close | `lookback_bars`, `max_distance` | no | yes | distance to a swing high/low |

Atoms compute from candle windows and given indicator facts; HELIOS holds no indicator library (`market_fact.py` module docstring). No atom for: trend state, pullback/retracement, accumulation/compression, liquidity sweep, reclaim, continuation (MISSING). HSA catalogue defines six such concepts (`catalogue/atomic/*.json`: `level_retest`, `range_compression`, `structure_break`, `engulfing_candle`, `momentum_thrust`, `volatility_expansion`) but HELIOS has no implementation of any (PROVEN by absence in the table above).

### 1.2 Registries (PROVEN)

- `AtomRegistry` (`helios/strategies/registry.py:35`): `register` refuses non-atoms and duplicate names; `implementation_for(name)` refuses unknown names ("HELIOS will not guess"); `build(package)` binds an ATOMIC package to the implementation whose `ATOM_NAME` equals the package `strategy_id`, then checks inputs, resolves parameters and runs `validate_binding`; `build_all` orders by canonical identity. `default_registry()` is in `catalogue.py`.
- Other registries: none found (`grep` for `class .*Registry` in `helios/`: one hit). `bind_strategies` (`helios/runtime/service.py:144`) binds a resolved handoff; fact names are a closed constant (`FACT_FIELDS`, `market_fact.py:43`); timeframes a closed enum (`timeframe.py:32-37`). No chain registry and no plugin discovery (MISSING by design).
- Consequence PROVEN by code: an atom implementation is addressable only by its own name, so the same implementation cannot be reused under a second `strategy_id`; a second behaviour needs a second class.

### 1.3 Strategy package model (PROVEN unless stated)

| Property | Evidence |
|---|---|
| Format and version | YAML or JSON, `schema_version: helios.strategy_package/1.0.0` (`spec/model.py:45`); unknown version refused (`:481-490`); JSON Schema export `docs/schema/strategy_package.schema.json` with Python-only rules named (`spec/schema.py`) |
| Closed world | unknown keys refused (HELIOS base model); decimals parsed exactly (`spec/loader.py`) |
| Kinds | `ATOMIC` and `CHAIN`; an ATOMIC package has no chain section |
| Composition | `ALL`, `ANY`, `SEQUENCE`, `CONTEXT_TRIGGER` (`spec/model.py:53-59`); at least two components; the same `strategy_id` may not appear twice in one chain (`:401`); SEQUENCE requires contiguous `sequence_index` and `ordering_window_seconds` (`:405-440`); CONTEXT_TRIGGER requires exactly one CONTEXT and one TRIGGER role (`:446-460`) and evaluates any components between them as extra "must hold" conditions with no ordering among them (`composition/primitives.py:166-175`) |
| Deterministic state model | seven states `DORMANT FORMING MATCHED ACTIVE WEAKENING INVALID EXPIRED` (`contracts/state.py:4,128-143`), legal transitions table, lifecycle timestamps (`contracts/lifecycle.py`) |
| Direction | `DIRECTIONAL`/`NON_DIRECTIONAL`; chain direction resolved from the first component that holds, is declared `SAME` and publishes LONG or SHORT; per-component relationship `SAME`, `OPPOSITE`, `ANY` (`composition/direction.py`) |
| Persistence and expiry | per package: `min_matched_frames`, `weakening_enabled`, expiry `NEVER`/`FRAMES`/`DURATION` (`spec/model.py:69-72,285`); a component keeps counting for its own published validity window (`docs/COMPOSITION.md` §3 notes) |
| Immutable identity | `strategy_id` pattern `^[a-z][a-z0-9_]{2,63}$`, strict `MAJOR.MINOR.PATCH` (`contracts/identity.py:53-73`); a chain names exact component versions and never binds the nearest version (`integration/hsa_boundary.py:99+`, docstring lines 1-30) |
| HSA package handoff | `load_handoff`/`resolve_handoff` resolve a directory set, report all unresolved references at once, refuse chain-of-chain (`hsa_boundary.py:161`). HSA's own monolithic package format is **not** loadable (TESTS-AND-REPRODUCTION 3; MISSING emitter) |
| CER identity compatibility | HELIOS carries `strategy_id`/`strategy_version`; CER-owned ids (`experiment_id`, `run_id`, `evidence_id`, `artifact_id`) are refused as HELIOS-authored (`contracts/identity.py:31-50`; `tests/test_contract_cer.py`). Live CER interplay UNVERIFIED |
| Replay fixtures | HERMES-style facts (`fixtures/hermes/`), packages (`fixtures/strategy_packages/`), handoff fixtures (`fixtures/hsa/`); deterministic replay feed (`runtime/feed.py`); multi-timeframe chain tests (`tests/test_chain_multi_timeframe.py`, including 4- and 5-stage sequences on arbitrary role names) |
| Common-language gap | `goal.strategy/v1` has only a PID candidate in GOAL; no schema text (G-22). Conformance PARTIAL by comparison with the PID candidate's scope list (primitives match; states match; fingerprint rule not present in HELIOS: MISSING) |

## 2. Can the proposed Trend-Pullback-PO3 design be expressed without changing HELIOS v1 semantics?

Candidate (as stated by the directive): bidirectional H4 trend, then H1 pullback, then M15 accumulation / sweep / reclaim, then M5 continuation.

**Answer: partly. The staged skeleton is expressible with no semantic change; the stage contents are not expressible with the six shipped atoms; two structural options would need a semantic extension and are not available.**

### 2.1 What is expressible as shipped (PROVEN)

- A four-stage multi-timeframe chain H4 / H1 / M15 / M5 with ordered stages and a single ordering window exists in HELIOS today (`fixtures/strategy_packages/scenario/gold_continuous_sequence.chain.yaml`: SEQUENCE, roles CONTEXT, LOCATION, CONFIRMATION, TRIGGER, window 28800 s), and arbitrary role names and five stages are tested (`tests/test_chain_multi_timeframe.py:105-190`, passing in the 1000-test run). So "four timeframes, ordered" is not a limit.
- Bidirectionality is expressible: one chain with `DIRECTIONAL`, `FROM_COMPONENTS`; the trend stage as the first `SAME` component sets LONG or SHORT per evaluation; the pullback stage can be declared `OPPOSITE` and the reclaim and continuation stages `SAME` (`direction.py`).
- A stage that is momentary in the market (a sweep) can stay countable through its own atomic persistence and expiry, because a component keeps counting for its own validity window; whether a particular window length is workable is a design question, UNVERIFIED by test.
- Three sub-steps on M15 can occupy three roles bound to the same timeframe (roles must be unique; timeframes need not be, per `spec/model.py:496-500` role check), each with its own atom, ordered by `sequence_index`; ties in match instant are in order (`primitives.py:88-110`).

### 2.2 Where expressibility fails (each PROVEN from the cited code)

| # | Failure | Cause (file) | Effect on the candidate |
|---|---|---|---|
| F1 | No atom exists for trend state, pullback, accumulation, sweep, reclaim or continuation | `catalogue.py` ATOM_TYPES (six atoms) | none of the five stage conditions can be written as a package today. `golden_cross` is an edge (a cross), not a standing trend state (`golden_cross.py:50`), so it does not hold "while the trend persists" beyond its own expiry |
| F2 | One atomic package consumes exactly one semantic input | `strategies/base.py:269` | a stage that needs two timeframes or two data series (for example a sweep of an H1 level observed on M15) cannot be one atom; it must be one atom on one timeframe, or split |
| F3 | Chain-of-chain is refused | `spec/model.py:346`; `hsa_boundary.py:161`; `docs/COMPOSITION.md:47` | "H4 trend frames a nested SEQUENCE of [H1, M15, M5]" or "a three-step M15 setup as its own chain" cannot be written |
| F4 | The same component `strategy_id` cannot appear twice in one chain, and an implementation is bound only by its name | `spec/model.py:401`; `registry.py` `build` | the same condition on two timeframes (for example a rejection pattern on both M15 and M5) needs two differently named atom classes |
| F5 | SEQUENCE has one ordering window across all stages, measured from the earliest to the latest first-match instant | `primitives.py:88-160` | an H4 trend that began long before the later stages stretches the window so far that the H1-to-M5 order is no longer tightly bounded; there is no per-stage window |
| F6 | CONTEXT_TRIGGER orders only CONTEXT before TRIGGER; intermediates are unordered | `primitives.py:166-200` | CONTEXT_TRIGGER can carry the four timeframes but cannot enforce pullback-then-reclaim order |
| F7 | Fact set is closed; HERMES publishes no swing, pivot, session-liquidity or SMA facts, and has no regime | `market_fact.py:27-43`; CONTRACT-MAPPING | any stage needing a fact outside `FACT_FIELDS` needs a contract decision (`HERMES-FACT-001`), not an atom |
| F8 | No per-stage memory before the chain first matches | `docs/COMPOSITION.md` section 6 (a resolved occurrence latches INVALID/EXPIRED; a live chain whose condition stops holding goes INVALID); `primitives.py` evaluates components at the current evaluation | once a chain has matched it latches and is voided correctly; before its first match, a stage that was true earlier but has lapsed is not remembered, so every stage must be live (through its own expiry) at the composing evaluation |

### 2.3 Minimal options (listed only; not designed, not implemented, no change made here)

1. **New atoms only**: add additive, single-input atoms for the missing conditions (F1), named distinctly (F4), composed with SEQUENCE across four roles. No composition semantics change; needs a PO decision, new tests, and facts that already exist (F7 permitting). This is the only option that leaves v1 semantics untouched.
2. **Composite atoms per stage** (for example one M15 atom that detects accumulation then sweep then reclaim within its own lookback), which satisfies F2 and F3 without nesting. Trade-off: the stage logic hides inside code rather than being visible as ordered chain components.
3. **CONTEXT_TRIGGER with intermediates** as a fallback where order among the middle stages is not required (F6).
4. **Two governed chains evaluated side by side**, correlated downstream by another component. Moves the join outside HELIOS; needs a CA decision because the trigger contract belongs to `GOAL-TRIGGER-001`.
5. **Semantic extensions (chain-of-chain, multi-input atoms, per-stage windows, chain-level latch)**: each changes v1 semantics, would need explicit architecture authority and a new package schema version, and **are not proposed or assumed here**.

No capability was extended and no package or atom was written for this answer.

## 3. Facts the candidate would need (information only)

Whatever atoms are written must draw on the published fact set: candle OHLC and volume, `ema_9/21/50/200`, `rsi_14`, `atr_14` (with the method caveats G-07 and G-08), and the session label after translation (G-04). Presence on PROD is audit A's finding (UNVERIFIED here).
