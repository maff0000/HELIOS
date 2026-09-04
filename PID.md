# PID — HELIOS v1 Clean-Slate Strategy Engine

## Product outcome

Build a completely new HELIOS as the deterministic strategy-state engine for THE GOAL GOLD trading stack.

HELIOS consumes HERMES market facts and continuously evaluates defined independent atomic strategies and governed strategy chains, publishing consistent strategy/chain state for FALCON consumption.

This repository is the only authoritative new HELIOS implementation. Legacy HELIOS is historical archive only and must not influence this build.

Presentation target: `FUNCTIONAL_ONLY`.

## Core boundary

HELIOS is execution-blind.

HELIOS must never know:

* whether a trade was entered;
* whether NEO acted;
* broker/account state;
* position size;
* SL/TP/trailing-stop state;
* trade P&L;
* whether a trade was rejected;
* whether a trade closed.

Given identical HERMES input, strategy definition and HELIOS code/version, HELIOS must produce identical strategy state regardless of account, broker, NEO or TRON activity.

## HERMES relationship

HERMES is the market-fact authority.

HERMES produces defined signals, indicators and market facts for defined instruments at:

* 1m
* 5m
* 15m
* 1h
* 4h
* 1d

HELIOS consumes these facts.

HELIOS must not duplicate HERMES' general indicator/signal responsibility. Strategy-local derived logic is acceptable only where it is intrinsically part of the strategy definition rather than a reusable market indicator HERMES should own.

## Atomic strategy doctrine

HELIOS must support many independent atomic strategies concurrently.

Each atomic strategy has:

* its own code/module;
* declared required inputs;
* deterministic evaluation;
* own state;
* normalised output contract;
* explicit version.

Atomic strategies:

* are small and well defined;
* are execution-blind;
* do not know other strategies exist;
* do not call/reference/import another strategy directly;
* mechanically combine HERMES facts to determine their own state.

Representative proof atoms may include:

* golden/death cross;
* breakout;
* proximity to swing high/low;
* rejection wick;
* no-wick directional candle;
* momentum/volatility condition.

These examples prove the framework; they are not automatically production-alpha strategies.

## Strategy composition / chaining

A separate HELIOS composition layer combines normalised atomic-strategy outputs.

Initial canonical primitives:

* `ALL`
* `ANY`
* `SEQUENCE`
* `CONTEXT_TRIGGER`

Chains must support, where applicable:

* component strategy identity/version;
* component state;
* direction relationship;
* order/sequence;
* timing window;
* persistence;
* expiry;
* semantic timeframe role;
* provenance;
* explicit explanation of why the chain matched or did not match.

For v1, chains should consume atomic strategies directly. Do not introduce arbitrary recursive chain-of-chain complexity without explicit architecture authority.

## Multi-timeframe strategy semantics

Initial GOLD strategy-engineering template:

* `CONTEXT` -> 4H
* `LOCATION` / proximity -> 1H
* `CONFIRMATION` / proof -> 15M
* `TRIGGER` -> 5M

These are semantic roles, not global hard-coded timeframe rules.

HELIOS must support strategies with different timeframe mappings and strategies using fewer/more semantic stages.

## Strategy state semantics

HELIOS continuously publishes strategy status. A trade lifecycle does not exist inside HELIOS.

There is no HELIOS concept of:

* entry;
* re-entry;
* stop hit;
* trade win/loss;
* account risk.

A strategy may remain valid/active while downstream NEO/TRON enter, exit or re-enter multiple trades.

HELIOS v1 must define an explicit, deterministic state model suitable for atomic strategies and chaining. Candidate semantic concepts include:

* `DORMANT`
* `FORMING`
* `MATCHED`
* `ACTIVE`
* `WEAKENING`
* `INVALID`
* `EXPIRED`

FORGE may refine exact naming only if semantics remain explicit, testable and architecture-compatible. State transitions and temporal validity must be documented.

## Normalised output contract

HELIOS must publish a common outer contract for atomic and composite strategy state.

It must preserve, as applicable:

* `strategy_id`;
* `strategy_version`;
* `chain_id` / chain version;
* instrument;
* timeframe and/or semantic role;
* state;
* direction;
* strength/evidence fields where explicitly defined by strategy;
* first matched UTC timestamp;
* last matched UTC timestamp;
* active-since UTC timestamp;
* last-evaluated UTC timestamp;
* validity/expiry information;
* component provenance for chains;
* schema version;
* freshness/source metadata.

FALCON must not reverse-engineer strategy-specific formats.

## FALCON relationship

FALCON is the flight deck/contract aggregator.

HELIOS publishes deterministic strategy truth to FALCON.

HELIOS must not absorb FALCON aggregation, NEO decisioning, ARES news/risk, CER empirical evidence storage, or TRON execution responsibilities.

## HSA relationship

HSA is the strategy-engineering authority.

HSA defines/decomposes strategy specifications and chain definitions using the HELIOS Strategy Blueprint.

HELIOS must provide a stable strategy package/specification format HSA can target.

FORGE implements HSA-approved strategy specifications. If a strategy definition is ambiguous, implementation must fail/return for clarification rather than invent trading logic.

HSA may be developed in parallel; fixtures/contracts are acceptable until live integration exists, provided no incompatible local doctrine is created.

## CER relationship

HELIOS must use canonical strategy identity/version semantics compatible with CER.

HELIOS does not become the empirical evidence store.

Do not create a competing experiment/result repository inside HELIOS.

Runtime strategy state may be consumed/registered downstream, but CER owns durable empirical evidence semantics.

## Initial vertical slice

Build the smallest architecture-proving slice:

HERMES-compatible market-fact input
-> multiple independent atomic strategy evaluations
-> normalised atomic strategy state
-> composition engine
-> at least one non-trivial chain
-> normalised chain state
-> FALCON-compatible downstream contract

The proof set should include enough variety to demonstrate:

* independent concurrent atoms;
* direction semantics;
* multi-timeframe inputs;
* at least one ordered/temporal composition (`SEQUENCE` or equivalent);
* at least one context/trigger composition.

Do not build a large strategy library in v1.

## Determinism and isolation

Required invariants:

* one strategy cannot mutate another strategy's state directly;
* one failed strategy evaluation cannot corrupt unrelated strategy state;
* malformed strategy definitions fail loudly;
* missing/incompatible required HERMES facts fail according to explicit freshness/validity rules;
* strategy outputs are reproducible from identical ordered inputs and definitions;
* no strategy reads account/trade/broker state.

## Runtime / deployment

* development host: `dell-debian`;
* Docker-first;
* production later runs the same proven application/container image;
* only external config/secrets/manifests differ by environment;
* no config in source code;
* UTC everywhere;
* structured logs;
* health/readiness;
* clean shutdown/restart;
* no unnecessary distributed infrastructure.

## Security / operational hygiene

* no secrets in Git;
* no broker credentials;
* no public internal state service by default;
* explicit config validation;
* fail loudly on invalid required runtime configuration;
* excellent Git hygiene.

## Acceptance criteria

### Clean-slate proof

* no dependency/import/reference to legacy HELIOS implementation;
* authoritative repository contains only new design/code.

### Atomic-engine proof

* multiple atomic strategies run concurrently;
* each is independently testable;
* each produces the normalised contract;
* strategy isolation is proven.

### Chain-engine proof

* `ALL` and/or `ANY` basic composition works;
* at least one ordered temporal `SEQUENCE` works;
* at least one `CONTEXT_TRIGGER` style chain works;
* direction/timing/persistence semantics are tested;
* chain output preserves component provenance.

### Multi-timeframe proof

Prove a representative GOLD-style semantic chain across 4H/1H/15M/5M fixtures or equivalent test feed without hard-coding those timeframes globally.

### Execution-blind proof

Tests must demonstrate HELIOS has no dependency on trade/account/broker/P&L state and behaves identically regardless of simulated downstream execution outcomes.

### Contract proof

* HERMES-compatible input contract works;
* malformed/incompatible input fails loudly;
* FALCON-compatible output contract is documented and demonstrated;
* HSA-targetable strategy specification/package exists;
* CER-compatible strategy identity/version fields exist.

### Runtime proof

* clean Docker build/start on `dell-debian`;
* health/readiness work;
* representative live-running evaluation works;
* restart is clean;
* UTC/logging/freshness behaviour is observable.

## Required tests

At minimum:

* atomic strategy unit tests;
* state-transition tests;
* multi-strategy isolation tests;
* chain primitive tests;
* temporal expiry/persistence tests;
* direction compatibility tests;
* multi-timeframe fixture tests;
* malformed/missing/stale input tests;
* deterministic replay test;
* Docker/runtime integration test;
* restart test;
* contract fixture tests for HERMES/FALCON/HSA/CER boundaries.

## Explicit non-goals

Do not build into HELIOS:

* NEO decision logic;
* TRON execution;
* account risk;
* MT5/broker integration;
* P&L/trade history;
* trailing stops;
* ARES news processing;
* CER empirical storage;
* backtesting engine;
* strategy auto-tuning;
* speculative dashboard/UI;
* equities/crypto expansion.

## Definition of complete

HELIOS v1 is complete only when:

* new Docker-first runtime works on `dell-debian`;
* execution-blind architecture is proven;
* atomic strategy framework is proven;
* composition/chaining is proven;
* multi-timeframe semantics are proven without global hard-coding;
* HERMES input and FALCON output contracts are demonstrated;
* HSA strategy-package target is documented/proven;
* CER identity compatibility is documented/proven;
* failures/restart behave correctly;
* Git history is clean and auditable;
* FORGE's fresh-context independent Auditor returns `PRODUCT_GREEN` against this PID.

Passing strategy unit tests alone is insufficient.
