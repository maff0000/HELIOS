# Integrating with HELIOS

One document for the four systems HELIOS touches. If you are building on any
of them, this is the page to read; `docs/CONTRACTS.md` is the reference for the
types themselves.

HELIOS is the deterministic, execution-blind strategy-state engine. It consumes
HERMES market facts, evaluates independent atomic strategies and governed
chains, and publishes normalised strategy state. Given identical ordered facts,
identical strategy definitions and an identical code version it produces
identical state — byte for byte — regardless of what any downstream system
does.

| boundary | direction | HELIOS's obligation |
|---|---|---|
| **HERMES** | HELIOS consumes | accept well-formed facts, refuse everything else loudly, never recompute an indicator |
| **FALCON** | HELIOS publishes | one normalised envelope for atomic and chain state, byte-stable, self-describing, schema-versioned |
| **HSA** | HELIOS runs what HSA authors | load declarative strategy packages, and refuse precisely rather than invent trading logic |
| **CER** | identity compatibility only | originate and carry `strategy_id` / `strategy_version`; never become an evidence store |

**This repository is public.** Nothing here contains a hostname, port,
credential, connection string or any other piece of deployment topology, and
nothing ever should. Every boundary below is a *data* contract; how bytes move
between hosts is a deployment concern, configured externally.

---

## 1. FALCON — what HELIOS publishes

### 1.1 The envelope

**One** envelope is published for atomic strategies and chains alike, so FALCON
never reverse-engineers a strategy-specific format. Its fields are listed in
`docs/CONTRACTS.md` §3; the machine-readable definition is
[`docs/schema/strategy_state.schema.json`](schema/strategy_state.schema.json).

Current version: **`helios.strategy_state/1.0.0`**.

### 1.2 The wire format

Canonical JSON, one object per line (JSON Lines), terminated by `\n`:

* object keys sorted, no insignificant whitespace — the same state always
  produces the same bytes, on every host and every run;
* decimals as exact normalised **strings** (`"2400"`, `"0.75"`), never JSON
  numbers, because binary floating point cannot carry them without loss;
* instants as fixed-width `YYYY-MM-DDTHH:MM:SS.ffffffZ`, always UTC;
* `null` preserved rather than the key omitted — an absent fact is published as
  visibly absent, so **every** field is present in every payload;
* `NaN` / `Infinity` refused rather than emitted as invalid JSON.

Each line stands alone as a complete document. Nothing is added at publication
time: no publication timestamp, no sequence number, no host identity. That is
what makes two runs diffable.

### 1.3 Validate, don't reverse-engineer

`docs/schema/strategy_state.schema.json` is generated from the model itself and
a test fails if the checked-in copy drifts, so it cannot quietly stop describing
what HELIOS emits. Regenerate with:

```sh
python3 -m helios.publish.schema
```

Note it describes what HELIOS **emits**, which is stricter than what the model
would accept on input: every field required, decimals as strings, instants
pinned to the exact canonical pattern, and `additionalProperties: false`
throughout.

### 1.4 Golden contract fixtures

`fixtures/falcon/` holds four exemplar published payloads. Code against these:

| file | what it exemplifies |
|---|---|
| `atomic_matched.json` | an atomic strategy whose condition matched |
| `atomic_no_match.json` | a non-match — still published, with an explanation |
| `chain_matched.json` | a chain, with per-component provenance |
| `chain_expired.json` | an occurrence that aged out, with its validity window |

Each file is self-describing: `canonical_json` is the exact line HELIOS
publishes, `payload` is the identical content indented for reading, and
`expectations` says what the fixture is there to prove. They are regenerated
from the contract layer and compared byte for byte by
`tests/test_contract_falcon.py`, so they cannot drift from reality.

```sh
python3 -m helios.integration.exemplars      # regenerate
```

### 1.5 Schema-version negotiation — the rule

**A consumer that does not recognise the published `schema_version` must refuse
the payload rather than interpret it.** That is the entire reason the field is
published, and it is the one piece of behaviour a FALCON integrator has to get
right.

1. `schema_version` is `<namespace>/<major>.<minor>.<patch>`. HELIOS publishes
   exactly one namespace: `helios.strategy_state`.
2. A consumer declares the **exact set** of versions it understands. Anything
   else is refused. HELIOS deliberately ships no "same major is probably fine"
   rule — guessing at an unfamiliar payload is the reverse-engineering this
   contract exists to prevent. A consumer that wants leniency states which
   versions it accepts, deliberately.
3. Refusal happens on the declared version **before** the payload is
   interpreted, so an unfamiliar document never has to be parsed at all.
4. HELIOS bumps the version deliberately: **major** when a field is removed or
   renamed, a field's meaning changes, or an accepted value set narrows;
   **minor** when a field is added or a value set widens; **patch** when only
   documentation changes and the bytes do not.
5. The envelope is a closed world — unknown fields are rejected on read — so
   even an additive change is visible to a strict consumer. That is intended:
   an unexpected field is a loud upgrade decision, never a silent
   pass-through.
6. HELIOS refuses in both directions. It will not construct, publish or parse an
   envelope carrying a version this build does not implement.

The reference implementation is `helios.publish.negotiation`:

```python
from helios.publish import accept_payload, negotiate_schema_version

envelope = accept_payload(line)                       # negotiate, then interpret
negotiate_schema_version(declared, my_accepted_set)   # or just the version check
```

### 1.6 Where the bytes go

HELIOS writes JSON lines to a **sink**, chosen by external configuration. There
is deliberately **no public internal state service** — the PID forbids one by
default — so HELIOS opens no socket, binds no port and speaks no wire protocol.
A consumer reads a file or a stream.

| environment variable | TOML (`HELIOS_CONFIG_FILE`) | meaning |
|---|---|---|
| `HELIOS_PUBLICATION_SINK` | `[publication] sink` | `FILE`, `STREAM` or `MEMORY` |
| `HELIOS_PUBLICATION_PATH` | `[publication] path` | file to append to (`FILE` only) |
| `HELIOS_PUBLICATION_STREAM` | `[publication] stream` | `STDOUT` or `STDERR` (`STREAM` only) |

```toml
[publication]
sink = "FILE"
path = "/var/lib/helios/strategy_state.jsonl"
```

No publication value is defaulted in source. These settings are loaded and
validated by `helios.config.load_config()` — the single configuration entry
point — alongside freshness, logging and the rest, and reached as
`config.publication()`. The environment wins over the file, so one container
image runs in every environment and only the injected environment differs.
Missing or invalid configuration is fatal at startup and every problem, of any
kind, is reported in one error. A setting belonging to a *different* sink is
a problem too — quietly ignoring it is how a deployment ends up publishing
somewhere nobody reads — unless a higher-precedence layer overrode it, which is
an override rather than a mistake.

`MEMORY` publishes nowhere durable; it exists for tests and dry runs.

`FILE` appends and flushes every record, so a consumer tailing the file sees
each state as it is published. The parent directory must already exist: HELIOS
does not create a destination it was not configured for.

---

## 2. HERMES — what HELIOS consumes

HERMES is the market-fact authority. HELIOS consumes indicators as **given
facts** and never recomputes them: there is deliberately no reusable indicator
library in this codebase. `helios.integration.hermes_boundary.describe_input_contract()`
returns this section in machine-readable form, derived from the contract models.

**This is a data contract, not a client.** HELIOS v1 ships no live HERMES
client, no database driver and no connection detail of any kind. Facts arrive
as validated values — from `fixtures/hermes/` today — and the rules below do
not change when the transport does.

### 2.1 Timeframes

`M1` (`1m`), `M5` (`5m`), `M15` (`15m`), `H1` (`1h`), `H4` (`4h`), `D1` (`1d`).
Both the HELIOS code and the HERMES code parse; the HELIOS code is always what
is published.

There is **no global mapping from timeframe to semantic role**. The PID's GOLD
template (`CONTEXT`→4H, `LOCATION`→1H, `CONFIRMATION`→15M, `TRIGGER`→5M) is
per-strategy configuration declared in a strategy package.

### 2.2 The fields HELIOS requires

Every fact is one instrument, one timeframe, one bar. `timestamp_utc` is the bar
**open** instant, timezone-aware UTC.

**Candle — all required:**

| field | notes |
|---|---|
| `open`, `high`, `low`, `close` | positive; `high >= max(open, close)`, `low <= min(open, close)` |
| `volume` | not negative |
| `complete` | whether HERMES considers the bar closed |
| `source` | the fact source as HERMES names it |

**Indicators — all optional, absent is `None` and never zero:**
`rsi_14` (0..100), `ema_9`, `ema_21`, `ema_50`, `ema_200`, `atr_14` (not
negative), `regime`, `session`.

`regime` and `session` are validated for shape only; HERMES owns those
vocabularies, so an unrecognised-but-well-formed value is a fact HELIOS does not
understand, not malformed input.

**Provenance — all required:**

| field | notes |
|---|---|
| `source` | the feed name |
| `schema_version` | `<namespace>/<major.minor.patch>`; a deployment configures which it accepts |
| `observed_at_utc` | when the authority produced the fact |
| `ingested_at_utc` | when HELIOS received it; must not precede `observed_at_utc` |

Numbers are exact decimal text. **Binary floats are refused at the boundary**:
`0.1 + 0.2 != 0.3`, and HELIOS promises identical output for identical inputs.

### 2.3 Ordering and freshness

Frames for one `(instrument, timeframe)` must be **strictly ascending** by
bar-open instant. Gaps are fine — markets close. Duplicates and out-of-order
frames are malformed input and are **never silently re-sorted**.

A fact is usable while its age, measured from the bar's **close** instant, is at
most `(timeframe duration × max_age_multiplier) + grace`, with optional
per-timeframe absolute overrides. Every one of those numbers comes from external
configuration (`docs/CONTRACTS.md` §7); there are no policy defaults in source.
The resulting verdict is published in the envelope's `inputs`, so a consumer can
see exactly how fresh the facts behind a state were.

### 2.4 What HELIOS refuses, loudly

Nothing is silently defaulted, silently zeroed or silently substituted. Every
rejection names the instrument, timeframe and offending instant.

* a binary float anywhere a price or indicator value is expected;
* a field HELIOS does not know — the fact contract is a closed world, so a
  HERMES schema addition is a deliberate HELIOS version change, never a silent
  pass-through;
* an incoherent candle (high below low, open/close outside the range);
* a negative volume, or `rsi_14` outside 0..100;
* a naive timestamp, or one that is not a plausible bar-open instant;
* an unsupported timeframe;
* provenance whose `ingested_at_utc` precedes its `observed_at_utc`;
* a window mixing instruments or timeframes;
* duplicate or out-of-order frames;
* a fact whose `schema_version` this deployment is not configured to accept;
* a fact older than the configured freshness limit;
* an incomplete bar, unless the configured policy allows one;
* a required indicator that is absent — a strategy that declared it needs 200
  bars never quietly evaluates against 12.

```python
from helios.integration import accept_market_facts

window = accept_market_facts(
    frames, policy=policy, now_utc=now, accepted_schema_versions=accepted
)
```

---

## 3. HSA — authoring strategies HELIOS runs

HSA is the strategy-engineering authority. It emits declarative **strategy
packages**; HELIOS loads and runs them. The format, field by field, is
`docs/CONTRACTS.md` §5, and the JSON Schema is
[`docs/schema/strategy_package.schema.json`](schema/strategy_package.schema.json).

Current version: **`helios.strategy_package/1.0.0`**. JSON or YAML.

A realistic, complete handoff is checked in at `fixtures/hsa/handoff/`: four
atomic packages and two chains (`SEQUENCE` and `CONTEXT_TRIGGER`) covering the
full GOLD 4H/1H/15M/5M template.

### 3.1 The handoff

1. **HSA emits** a set of packages — atomic strategies and the chains built
   from them.
2. **HELIOS validates each package** on load: schema version, closed-world key
   set, known market-fact fields, parameter ranges, role uniqueness, chain
   primitive rules, expiry coherence (`docs/CONTRACTS.md` §5.1).
3. **HELIOS then resolves the handoff as a whole.** This is the check a single
   package cannot make:
   * every chain component must resolve to a package actually in the handoff,
     **at the exact version named**;
   * a chain component must be an **atomic** strategy — v1 chains consume atoms
     directly, and chain-of-chain composition needs explicit architecture
     authority per the PID;
   * two definitions claiming one promoted identity are refused, because
     published state would stop being reproducible.
4. **HELIOS reports back** what it accepted: the identities it holds, each
   package's role-to-timeframe bindings, and the components each chain resolved
   to (`describe_handoff`).

```python
from helios.integration import load_handoff, describe_handoff

bundle = load_handoff("/etc/helios/strategies")   # raises on anything unresolved
report = describe_handoff(bundle)
```

### 3.2 HELIOS never invents trading logic

Every ambiguity is a loud `StrategySpecError` naming the offending file and the
exact thing that is unresolved. HELIOS does not pick a default, does not derive
a fact HERMES has not published, and does not bind to a near-miss version.
`fixtures/hsa/` demonstrates each refusal:

| case | what HELIOS says |
|---|---|
| `underspecified/sequence_without_ordering_window.chain.yaml` | a `SEQUENCE` must declare `ordering_window_seconds` — "in order, within what period?" has no safe default |
| `underspecified/requires_unpublished_fact.atomic.yaml` | the package requires `ema_100`; HELIOS will not invent a fact HERMES does not publish |
| `unresolved/missing_component/` | the chain requires `range_breakout@1.0.0` and the handoff contains no package for that `strategy_id` at any version |
| `unresolved/version_mismatch/` | the chain requires `golden_cross@2.0.0`; HELIOS holds `['1.0.0']` and **will not substitute a different version** |
| `unresolved/chain_of_chains/` | the chain names another chain as a component |

The version rule deserves its own sentence, because it is the one that looks
unhelpful and is not: **a version is not a hint.** Binding to the nearest
available version would publish state under an identity CER records evidence
against, produced by a definition nobody approved.

Every unresolved reference in a handoff is reported at once, so HSA fixes one
handoff rather than discovering faults one attempt at a time.

### 3.3 What HELIOS guarantees back

* Every package HELIOS accepts publishes state under **exactly** the identity
  and version HSA declared.
* The role-to-timeframe mapping is taken **verbatim** from the package. HELIOS
  holds no global opinion about which timeframe is "the context".
* Evaluation is deterministic: identical ordered facts and an identical package
  produce an identical envelope, byte for byte.
* A strategy is handed only its declared inputs, its declared parameters, a
  freshness policy and its own previous state. There is no channel through
  which downstream activity can reach it.
* HELIOS refuses rather than guesses, always, and says precisely what it could
  not resolve.

---

## 4. CER — identity compatibility

CER owns durable empirical evidence. **HELIOS does not, and must not, become an
evidence store**: there is no experiment, run, evidence or artifact record here,
no table, no storage and no competing registry.

### 4.1 Canonical identifiers

CER's canonical identity model names six identifiers. HELIOS originates two of
them and carries them cleanly on **every** envelope it publishes, atomic and
chain alike, so a consumer never branches on `kind` to learn who produced a
state:

| identifier | owner |
|---|---|
| `strategy_id` | **HELIOS** — `^[a-z][a-z0-9_]{2,63}$`, e.g. `golden_cross` |
| `strategy_version` | **HELIOS** — strict `major.minor.patch`; no leading zeros, no pre-release suffixes |
| `experiment_id` | CER |
| `run_id` | CER |
| `evidence_id` | CER |
| `artifact_id` | CER |

HELIOS neither mints nor publishes the bottom four, and a guard test asserts
those names never appear in a HELIOS payload. `evidence` in the envelope is
per-evaluation explanation, not a store.

The canonical single-string form is `golden_cross@1.0.0`.

```python
from helios.integration import canonical_identity, published_identity
```

### 4.2 A promoted version is immutable

**A logic change means a NEW version, never an in-place edit.** Otherwise
evidence CER recorded against a version would silently stop describing the
strategy that produced it.

That rule is easy to state and easy to violate by accident, so HELIOS makes it
mechanical. `definition_fingerprint(package)` is a deterministic hash of the
package's declared **behaviour** — inputs, parameters, direction, timing,
persistence, expiry, chain composition. Excluded are `metadata` (correcting a
description is not a logic change) and the version itself (the fingerprint's
job is to be compared *across* versions).

`assert_promotion_immutable(promoted, proposed)` then raises when a changed
definition reuses a promoted version, **and equally** when an unchanged
definition bumps one — a version that moves for no reason makes CER's record of
which version produced a result meaningless in the other direction.

Both functions are pure. Nothing is stored, and nothing here reads or writes
any repository of results.

---

## 5. Which boundaries are fixture-backed today

HSA and CER are being built in parallel with HELIOS, and there is no live
HERMES or FALCON connection in this work item. What that means concretely:

| boundary | status |
|---|---|
| **HERMES input** | **Fixture-backed.** The contract, the acceptance path and every refusal are real and enforced; facts come from `fixtures/hermes/` rather than a live feed. There is no HERMES client in HELIOS v1. |
| **FALCON output** | **Real, with fixture exemplars.** The envelope, canonical serialisation, JSON Schema, sinks and version negotiation are the production path. `fixtures/falcon/` holds exemplar payloads generated by that same path. No FALCON consumer has been connected. |
| **HSA packages** | **Fixture-backed.** The package format, loader, JSON Schema and handoff resolution are real; the packages in `fixtures/hsa/` stand in for HSA's own output until HSA emits them. The strategies they declare are illustrative, not approved alpha. |
| **CER identity** | **Compatibility and documentation only.** The identity fields, their patterns and the promotion rule are enforced. Nothing has been agreed with a running CER, and HELIOS stores no evidence either way. |

Fixtures go through **exactly the same contract validation** a live feed would,
so a fixture HELIOS accepts is one the real contract accepts. When a live
integration arrives it changes the transport, not the rules on this page.

---

## 6. Execution blindness — what you will not find

HELIOS has no concept of trade entry, position, stop loss, take profit,
trailing stop, P&L, win/loss, broker, account, order, fill or closure, and no
envelope field, evaluation input or configuration setting exposes one. This is
enforced mechanically rather than by convention:
`tests/test_execution_blind.py` scans every identifier, string literal and
fixture value in the tree against `tests/data/forbidden_execution_vocabulary.txt`.

If you are integrating and need one of those concepts, it belongs to NEO, TRON
or CER — not here. A strategy may remain `ACTIVE` while downstream systems act
on it any number of times, or never; HELIOS state is identical either way.
