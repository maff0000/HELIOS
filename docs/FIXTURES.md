# HERMES fixtures

Canonical, deterministic, hand-checkable market facts for `XAU_USD` across the
GOLD template timeframes. They are the offline stand-in for the live HERMES
feed, and they go through **exactly the same contract validation** as real
facts — a fixture HELIOS accepts is one the real contract accepts.

Load them with `helios.hermes.load_fixture(path)`.

## Layout

```
fixtures/hermes/
  xau_usd/     canonical facts, all valid and fresh at their reference instant
  malformed/   deliberately broken; each must fail loudly, one defect apiece
  stale/       structurally valid but too old, or with a forming last bar
```

## File format

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

## How the numbers were chosen

Prices are round, legible values around $2400 gold. OHLC is stated explicitly
per frame. Indicator values are **supplied facts chosen for legibility, not
recomputed series** — which is the point: HERMES owns indicators and HELIOS
consumes whatever it is told. A golden cross in the H4 fixture is a golden
cross because `ema_50` is stated to cross `ema_200`, exactly as the live feed
would assert it.

## Canonical fixtures and what each one proves

| file | timeframe | frames | proves |
|---|---|---|---|
| `xau_usd_h4.json` | H4 | 12 | golden cross: `ema_50` below `ema_200` for frames 0–5, above from frame 6 |
| `xau_usd_h4_death_cross.json` | H4 | 8 | death cross at frame 4 |
| `xau_usd_h1.json` | H1 | 24 | swing high at frame 8 (high 2413.50, series max), swing low at frame 15 (low 2392.50, series min) — swing proximity material |
| `xau_usd_m15.json` | M15 | 16 | bearish rejection wick at frame 5 (upper wick 0.846 of range), bullish at frame 9 (lower wick 0.857), no-wick bearish at frame 3, no-wick bullish at frame 12; every other frame under 0.60 |
| `xau_usd_m5.json` | M5 | 24 | range high exactly 2410.00 across frames 0–17, breakout at frame 18 (close 2413.50); momentum `rsi_14` 68→72 across 70; volatility `atr_14` doubles 1.20→2.40 |

Together they form an ordered temporal sequence across H4/H1/M15/M5 — enough
for a multi-timeframe semantic chain without any timeframe being hard-coded
globally.

## Malformed fixtures

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

## Stale fixtures

These are **structurally valid** — staleness is a policy verdict, not a
structural defect — so they load cleanly and are then refused by the freshness
rules.

| file | behaviour |
|---|---|
| `xau_usd_h4_stale.json` | same facts, judged three days later; `require_fresh_frame` raises `StaleFactError` |
| `xau_usd_h4_incomplete_last_bar.json` | latest bar still forming; refused when `allow_incomplete_frames` is false, accepted when true |

## Strategy package fixtures

`fixtures/strategy_packages/valid/` holds five loadable packages (three atomic,
a `CONTEXT_TRIGGER` chain, a four-stage `SEQUENCE` chain).
`fixtures/strategy_packages/malformed/` holds ten packages, each broken in
exactly one way, covering the failure rules in `docs/CONTRACTS.md` §5.1.
