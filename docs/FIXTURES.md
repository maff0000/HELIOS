# Fixtures

Everything checked in under `fixtures/`. Four sets, one per boundary:

```
fixtures/hermes/            market facts HELIOS consumes    (§1)
fixtures/strategy_packages/ strategy packages HSA authors   (§2)
fixtures/hsa/               whole HSA handoffs              (§3)
fixtures/falcon/            published payloads FALCON reads (§4)
```

A fifth set cuts across those: `fixtures/hermes/scenario/` and
`fixtures/strategy_packages/scenario/` are the time-aligned facts and the
strategy handoff the **running service** evaluates (§5).

## 1. HERMES fixtures

Canonical, deterministic, hand-checkable market facts for `XAU_USD` across the
GOLD template timeframes. They are the offline stand-in for the live HERMES
feed, and they go through **exactly the same contract validation** as real
facts — a fixture HELIOS accepts is one the real contract accepts.

Load them with `helios.hermes.load_fixture(path)`.

### 1.1 Layout

```
fixtures/hermes/
  xau_usd/     canonical facts, all valid and fresh at their reference instant
  malformed/   deliberately broken; each must fail loudly, one defect apiece
  stale/       structurally valid but too old, or with a forming last bar
```

### 1.2 File format

```json
{
  "fixture_schema_version": "helios.hermes_fixture/1.0.0",
  "fixture_id": "xau_usd_h4",
  "description": "What this fixture is.",
  "expectations": ["what a later work item may rely on this fixture to prove"],
  "instrument": "XAU_USD",
  "timeframe": "H4",
  "fact_schema_version": "hermes.market_fact/1.0.0",
  "source": "hermes",
  "reference_now_utc": "2026-01-07T00:01:00Z",
  "frames": [
    {
      "timestamp_utc": "2026-01-05T00:00:00Z",
      "candle": {
        "open": "2395.00", "high": "2397.50", "low": "2393.50",
        "close": "2396.00", "volume": "1000",
        "complete": true, "source": "hermes"
      },
      "indicators": {
        "rsi_14": "44.00", "ema_9": "2396.40", "ema_21": "2397.20",
        "ema_50": "2396.00", "ema_200": "2400.00", "atr_14": "6.00",
        "regime": "TRANSITION", "session": "ASIA"
      },
      "observed_at_utc": "2026-01-05T04:00:01Z",
      "ingested_at_utc": "2026-01-05T04:00:02Z"
    }
  ]
}
```

Notes:

* `timestamp_utc` is the bar **open** instant. Frames are strictly ascending
  and exactly one timeframe-duration apart.
* `reference_now_utc` is the instant freshness should be judged against. In
  every canonical fixture the latest bar is exactly **60 seconds** past its
  close, so all of them are fresh under a normal policy.
* Numbers are written as JSON **strings** and read as exact `Decimal`s.
  Binary floats are refused at the contract boundary (see
  `docs/CONTRACTS.md` §1.4).
* A frame may override `instrument`, `timeframe`, `source` or
  `fact_schema_version` locally; that is how the malformed fixtures inject a
  single defect.
* `expectations` is documentation of what the fixture proves. The substantive
  claims are asserted in `tests/test_hermes_fixtures.py`, so a fixture and its
  description cannot drift apart silently.

### 1.3 How the numbers were chosen

Prices are round, legible values around $2400 gold. OHLC is stated explicitly
per frame. Indicator values are **supplied facts chosen for legibility, not
recomputed series** — which is the point: HERMES owns indicators and HELIOS
consumes whatever it is told. A golden cross in the H4 fixture is a golden
cross because `ema_50` is stated to cross `ema_200`, exactly as the live feed
would assert it.

### 1.4 Canonical fixtures and what each one proves

| file | timeframe | frames | proves |
|---|---|---|---|
| `xau_usd_h4.json` | H4 | 12 | golden cross: `ema_50` below `ema_200` for frames 0–5, above from frame 6 |
| `xau_usd_h4_death_cross.json` | H4 | 8 | death cross at frame 4 |
| `xau_usd_h1.json` | H1 | 24 | swing high at frame 8 (high 2413.50, series max), swing low at frame 15 (low 2392.50, series min) — swing proximity material |
| `xau_usd_m15.json` | M15 | 16 | bearish rejection wick at frame 5 (upper wick 0.846 of range), bullish at frame 9 (lower wick 0.857), no-wick bearish at frame 3, no-wick bullish at frame 12; every other frame under 0.60 |
| `xau_usd_m5.json` | M5 | 24 | range high exactly 2410.00 across frames 0–17, breakout at frame 18 (close 2413.50); momentum `rsi_14` 68→72 across 70; volatility `atr_14` doubles 1.20→2.40 |

Each of these proves **one atom** over one hand-checked series, and each is
judged against its own `reference_now_utc`. That is deliberate and it has a
consequence worth stating here rather than discovering later: no single instant
makes all four simultaneously fresh, so they cannot serve a multi-timeframe
chain evaluated end to end over real windows. The time-aligned set in §5 exists
for that, and leaves these untouched.

### 1.5 Malformed fixtures

Each carries exactly one defect and must fail loudly, naming the file and the
frame index.

| file | defect |
|---|---|
| `missing_candle_field.json` | frame 2 has no `close` |
| `high_below_low.json` | frame 1 high beneath its low |
| `unknown_candle_field.json` | an unrecognised candle field (`vwap`) |
| `naive_timestamp.json` | timestamp with no UTC offset |
| `misaligned_timestamp.json` | M15 bar opening at 12:37 |
| `duplicate_timestamp.json` | two frames sharing one timestamp |
| `out_of_order.json` | frames not ascending — never silently re-sorted |
| `mixed_instrument.json` | a frame from another instrument |
| `unsupported_timeframe.json` | timeframe `H3` |
| `negative_volume.json` | negative volume |
| `rsi_out_of_range.json` | `rsi_14` above 100 |
| `unknown_fixture_schema.json` | unrecognised fixture format |
| `ingested_before_observed.json` | incoherent provenance |

### 1.6 Stale fixtures

These are **structurally valid** — staleness is a policy verdict, not a
structural defect — so they load cleanly and are then refused by the freshness
rules.

| file | behaviour |
|---|---|
| `xau_usd_h4_stale.json` | same facts, judged three days later; `require_fresh_frame` raises `StaleFactError` |
| `xau_usd_h4_incomplete_last_bar.json` | latest bar still forming; refused when `allow_incomplete_frames` is false, accepted when true |

## 2. Strategy package fixtures

`fixtures/strategy_packages/valid/` holds six loadable packages: four atomic
(`golden_cross`, `range_breakout`, `rejection_wick`, `swing_proximity`), a
`CONTEXT_TRIGGER` chain and a four-stage `SEQUENCE` chain. The set is a
**self-contained handoff**: every chain in it resolves against that directory
alone, which `tests/test_contract_hsa.py` asserts through the handoff resolver.
`swing_proximity@1.0.0` is deliberately the same definition as the reference
package shipped at `helios/strategies/packages/`, and a test holds the two
files to that — a promoted identity is immutable, so two files claiming one
version must describe one strategy.

`fixtures/strategy_packages/malformed/` holds ten packages, each broken in
exactly one way, covering the failure rules in `docs/CONTRACTS.md` §5.1.

## 3. HSA handoff fixtures

`fixtures/hsa/` is about a handoff as a **set**, which is a property no single
package has. Loading proves a package parses; resolving proves HELIOS can
actually run it.

| directory | what it is |
|---|---|
| `handoff/` | a realistic, complete HSA handoff: four atomic packages and two chains (`SEQUENCE` and `CONTEXT_TRIGGER`) covering the GOLD 4H/1H/15M/5M template. It resolves. |
| `underspecified/` | packages that parse but leave something HELIOS refuses to guess — a `SEQUENCE` with no ordering window, a package requiring a fact HERMES does not publish |
| `unresolved/missing_component/` | a chain naming a component the handoff does not contain |
| `unresolved/version_mismatch/` | a chain naming `golden_cross@2.0.0` when the handoff holds `1.0.0`; HELIOS will not substitute a version |
| `unresolved/chain_of_chains/` | a chain naming another chain as a component, which v1 refuses |

Load one with `helios.integration.load_handoff(directory)`; every unresolved
reference in the set is reported at once. See `docs/INTEGRATION.md` §3.

## 4. FALCON golden payloads

`fixtures/falcon/` holds four **exemplar published payloads** — the artefacts a
FALCON integrator codes against, covering an atomic match, an atomic non-match,
a matched chain and an expired chain.

Each file carries the payload, its exact `canonical_json` bytes, a description
and an `expectations` list. They are not hand-written: they are generated by
the real publication path (`helios.integration.exemplars`) and
`tests/test_contract_falcon.py` regenerates and compares them, so a golden file
cannot drift from what HELIOS actually publishes.

See `docs/INTEGRATION.md` §1.

## 5. The vertical-slice scenario

```
fixtures/hermes/scenario/            time-aligned facts, four timeframes  (§5.1)
fixtures/strategy_packages/scenario/ the handoff the runtime is configured with (§5.4)
```

This is the set the running service evaluates (`docs/RUNTIME.md`), and it
exists because of a gap the single-atom fixtures could not close.

### 5.1 Why a second fact set exists

Each fixture in §1.4 was authored to prove **one atom in isolation**, and each
carries its own `reference_now_utc`: H1 at `2026-01-06T00:01Z`, M15 and M5 at
`2026-01-06T16:01Z`, H4 at `2026-01-07T00:01Z`. That is correct for what they
are and fatal for what the PID's vertical slice needs — **no single evaluation
instant makes them simultaneously fresh**, so no multi-timeframe chain could
ever be evaluated end to end over real market-fact windows.

The §1.4 fixtures are therefore left exactly as they are; the atom tests assert
their precise contents. This set is new, and it is *coherent* rather than
merely simultaneous:

* **one clock** — one instrument, one reference instant
  (`2026-03-02T16:01:00Z`), one publication delay (60 seconds after each bar's
  close), and every timeframe fresh at every evaluated instant under the
  configured policy;
* **one market** — M5 is the base truth and the coarser series are its **exact
  aggregate** wherever they overlap: same open, same high, same low, same
  close, same volume. Four documents describing four different markets would be
  four fixtures, not a scenario;
* **stated expectations** — each document declares what it proves, and the
  numbers behind those claims are checked in `tests/test_scenario_fixtures.py`,
  so a document and its description cannot drift apart.

Indicators are **not** aggregated. They are supplied HERMES facts chosen for
legibility, exactly as in §1.3 — HELIOS consumes indicators and never computes
them, and re-deriving them here would assert the opposite.

### 5.2 What the documents hold

| file | timeframe | frames | span (bar opens) | supplies |
|---|---|---|---|---|
| `xau_usd_aligned_h4.json` | H4 | 8 | 2026-03-01 08:00 → 03-02 12:00 | `CONTEXT`: `ema_50` − `ema_200` runs −6.00, −5.00, −4.00, −3.00, −2.50, −1.00, **+1.25**, +2.00 — one crossing, on the 03-02 08:00 bar |
| `xau_usd_aligned_h1.json` | H1 | 29 | 2026-03-01 11:00 → 03-02 15:00 | `LOCATION`: swing high **2416.00** (03-01 16:00), swing low **2380.00** (03-02 00:00); the 12:00 bar closes 2408.00, then 2411.50, 2413.00, 2414.00 — each nearer the high, none past it |
| `xau_usd_aligned_m15.json` | M15 | 28 | 2026-03-02 09:00 → 15:45 | `CONFIRMATION`: dominant **lower** wicks on the 13:30 (0.862069), 13:45 (0.642857) and 14:00 (0.857143) bars; every other bar under 0.60 either side |
| `xau_usd_aligned_m5.json` | M5 | 84 | 2026-03-02 09:00 → 15:55 | `TRIGGER`: the 13:55 bar closes **2411.50**, clearing the prior 19-bar range high **2408.50** by more than `0.5 × atr_14 2.40`. No other bar clears its own prior range |

The narrative is one session, told at four resolutions: a 4H trend change into
the London afternoon, price grinding up toward a swing high a day old, buyers
repeatedly defending a shelf around 2398–2402 on the quarter-hour, and finally
a five-minute break of the range those defences built.

### 5.3 The evaluation instants

A bar's facts arrive 60 seconds after it closes, so the service evaluates at
`close + 60s` of every M5 bar. The replay begins at the first instant where
every timeframe can satisfy the deepest lookback any strategy **declared** of it
— H1 needs 24 bars, M5 needs 20 — which is `2026-03-02T11:01:00Z`, and runs to
`16:01:00Z`: **61 evaluations**, five minutes apart.

At every one of them all four timeframes are fresh. At the last, the H4 bar is
2 hours 1 minute old against a limit of 6 hours 1 minute; the other three are
60 seconds old.

### 5.4 The handoff it is evaluated against

`fixtures/strategy_packages/scenario/` is self-contained: the four atomic
definitions from §2, **copied verbatim** — a promoted identity is immutable, and
`tests/test_scenario_packages.py` holds every copy in the repository to one
definition fingerprint — plus two chains that exist only here:

| package | primitive | why it is a new identity |
|---|---|---|
| `gold_continuous_sequence@1.0.0` | `SEQUENCE`, `CONTEXT`→H4, `LOCATION`→H1, `CONFIRMATION`→M15, `TRIGGER`→M5 | every component is satisfied by `MATCHED` **or** `ACTIVE` |
| `gold_continuous_context_trigger@1.0.0` | `CONTEXT_TRIGGER`, `CONTEXT`→H4, `TRIGGER`→M5 | the same, plus a `FRAMES` expiry counted in frames of the finest timeframe it binds |

That one difference matters because HELIOS evaluates **continuously**, far more
often than a component's own bar changes. A fifteen-minute confirmation that
matched at 13:46 publishes `ACTIVE` on the next four evaluations against the
same still-current bar; the match is no less true, it is simply no longer new.
A chain accepting only `MATCHED` could hold for exactly one evaluation, which
describes a chain nobody could act on. A behaviour change is a **new**
definition, so these carry new identities rather than editing a promoted one.

### 5.5 What the slice does

Every state below is asserted in `tests/test_vertical_slice.py`, and every
number the states depend on is checked independently in
`tests/test_scenario_fixtures.py`.

```
instant   golden_cross  swing_prox.  rejection_wick  range_breakout   SEQUENCE   CONTEXT_TRIGGER
11:01     DORMANT       DORMANT      DORMANT         DORMANT          DORMANT    DORMANT
12:01     MATCHED       DORMANT      DORMANT         DORMANT          FORMING    FORMING
13:01     ACTIVE        MATCHED      DORMANT         DORMANT          FORMING    FORMING
13:46     ACTIVE        ACTIVE       MATCHED         DORMANT          FORMING    FORMING
14:01     ACTIVE        ACTIVE       ACTIVE          MATCHED          MATCHED    MATCHED
14:06     ACTIVE        ACTIVE       ACTIVE          ACTIVE           ACTIVE     ACTIVE
14:31     ACTIVE        ACTIVE       INVALID         ACTIVE           INVALID    ACTIVE
15:06     ACTIVE        ACTIVE       DORMANT         EXPIRED          FORMING    EXPIRED
16:01     ACTIVE        ACTIVE       DORMANT         DORMANT          FORMING    FORMING
```

The stages establish in the order the `SEQUENCE` declares — `CONTEXT` at 12:01,
`LOCATION` at 13:01, `CONFIRMATION` at 13:46, `TRIGGER` at 14:01, a span of two
hours inside the declared eight-hour ordering window — and all four are still
holding at 14:01, which is what makes the chain match `LONG` with a strength of
`1.0000` and all four timeframes fresh.

The two chains then resolve **differently, on purpose**:

* the `SEQUENCE` goes `INVALID` at 14:31, when the M15 bar that becomes current
  carries no dominant wick and its confirmation is voided. The explanation names
  `rejection_wick@1.0.0` and why;
* the `CONTEXT_TRIGGER` goes `EXPIRED` at 15:06 — its own declared validity
  (12 frames of M5, one hour from 14:01) ran out. Nothing broke.

That is the state model's distinction between "something broke" and "time ran
out", demonstrated on one feed rather than asserted. Both then rearm through
`DORMANT` and settle at `FORMING`, because the H4 context and the H1 location
are still holding at the end of the session.
