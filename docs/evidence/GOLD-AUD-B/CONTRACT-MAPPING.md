# Actual versus expected contract (CA item 4)

Expected: HELIOS `MarketFactFrame` (HELIOS `helios/contracts/market_fact.py`, `7f7193d55475e40583892b94dd461b1b64d6f1ef`; advertised schema `helios.market_fact/1.0.0` at line 47). Actual: HERMES publication at `ca1901789f8c7a7e085d02ebde8f7264d90d669e`: candle (`utils/candle_contract_v1.py`), indicators (`utils/hermes_indicators_v1.py`, values from `utils/hermes_runtime_publisher_steps_v1.py:431-480`), sessions (`utils/hermes_sessions_v1.py`), control plane (`utils/hermes_control_plane_v1.py`), gaps/depth (`utils/hermes_gaps_v1.py:42-46`). Live PROD content is **UNVERIFIED** (Lane A output unavailable); where a live sample was used it is a single 2026-10-07 capture of one H1 bar (private), reduced here to field names and types. No decision below freezes any contract; `HERMES-FACT-001` owns fact names.

## A. Candle envelope to frame (field by field)

| HELIOS frame field | HERMES source field | Form difference | Result in HELIOS validators | Gap id |
|---|---|---|---|---|
| `instrument` | `data.instrument` | canonical `XAU_USD`; HERMES refuses the alias `XAUUSD` as a key | accepted; `XAUUSD` is also a well-formed HELIOS instrument string but a different instrument, with no mapping found | G-09 |
| `timeframe` | `data.timeframe` | codes `M1 M5 M15 H1 H4 D1`; HERMES also recognises legacy `D` (`candle_contract_v1.py:32`) | HELIOS parses `D1`/`1d` (and `4H`, `5M`, `15M`, `1D`); only legacy `D` is not parsed | G-10 |
| `timestamp_utc` | `data.timestamp_utc` | millisecond ISO text ending Z; bar OPEN instant (close = open + TTL, `candle_contract_v1.py:173`) | accepted (probe A6 PASS) | none |
| `candle.open/high/low/close` | `data.open/high/low/close` | JSON numbers (binary floats), rounded to 6 places (`:131`, `:218`) | **refused as floats** (A1); accepted when parsed to exact decimals from the text | G-02 |
| `candle.volume` | `data.volume` | number | accepted after exact-decimal parse | G-02 |
| `candle.complete` | `data.is_closed` | name differs | **refused** when passed through (A4: field required, extra forbidden) | G-03 |
| `candle.source` | `provenance.source` (a lowercase vendor string) | lowercase string | accepted | none |
| `provenance.source` | `provenance.publisher`/`source` | no environment in the candle envelope | accepted, but DEV and PROD are indistinguishable | G-05 |
| `provenance.schema_version` | `schema_version` = `v1`, `contract` = `hermes.candles.latest` | frame demands `<namespace>/<major.minor.patch>` (`market_fact.py:176-184`) | **refused** (A2) | G-01 |
| `provenance.observed_at_utc` | `generated_at_utc` | ms ISO | accepted | none |
| `provenance.ingested_at_utc` | none (reader's clock) | introduces a wall-clock value at the adapter | accepted if not before observed | G-11 |
| (no frame field) | `status`, `freshness_state`, `valid_until_utc`, `reason_codes` | HERMES can publish `FORMING`, `STALE`, `GAP_DETECTED`, `SOURCE_INCOMPLETE`, `MARKET_CLOSED` (`:52-55`) | frame has no status; non-OK must be refused or mapped before translation | G-06 |
| (no frame field) | `data.gap_state`, `source_coverage`, `source_count`, `derivation_policy`, `provenance.derivation` | derived H4/D1 completeness accounting | lost in translation; HELIOS permits gaps in windows | G-06 |
| (no frame field) | `data.body_high ... candle_direction` geometry | HERMES-computed geometry | not used; HELIOS atoms compute their own | none (retained value) |

## B. Indicator object to frame indicators

Source: `hermes:indicators:XAU_USD:<TF>:v1` (`hermes_indicators_v1.py`); joined to the candle by `value_open_time_utc` equal to the candle `timestamp_utc`. Missing values are published as explicit null (never fabricated).

| HELIOS indicator field | HERMES name | Method declared by HERMES | Result | Gap id |
|---|---|---|---|---|
| `rsi_14` | `rsi_14` | `CUTLER_SMA_14` (simple average, not Wilder smoothing) | accepted; semantic difference from a conventional RSI | G-08 |
| `ema_9`, `ema_21`, `ema_50`, `ema_200` | same names | standard 2/(n+1), SMA seed | accepted; `ema_200` null until 200 bars exist | G-07 |
| `atr_14` | `atr_14` | `SMA_14` of true range (not Wilder) | accepted; semantic difference | G-08 |
| `regime` | none | HERMES forbids regime keys (`hermes_indicators_v1.py`, forbidden-term list) | MISSING by design (ARES-owned) | G-13 |
| `session` | separate key `hermes:sessions:XAU_USD:v1`, field `current_session` | lowercase names `asia london newyork overlap_ldn_ny off_hours` (`hermes_sessions_v1.py` SESSION_NAMES) | **refused** unless uppercased (A3, B2); also a per-instrument current value, not bound to a bar | G-04 |
| none | `ema_12`, `ema_26`, `bollinger_upper/middle/lower_20_2`, `adx_14`, `adx_plus_di_14`, `adx_minus_di_14` | various | **refused** if passed through (A5: extra forbidden); closed-world contract | G-04 |
| none | SMA (any period) | `sma` is a declared family but the wiring publishes none | MISSING | G-07 |

Indicator history (for closed bars other than the latest) is specified by HERMES work order `WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001`; PID-HERMES-MVP-001 (lines 100-112) states production activation had not occurred when written. Present PROD state UNVERIFIED.

## C. Provenance, version and environment

| Topic | HELIOS expects | HERMES actual | Note |
|---|---|---|---|
| fact schema version | `helios.market_fact/1.0.0` advertised; config may also accept `hermes.market_fact/1.0.0` | `v1` plus contract name `hermes.candles.latest` | PROVEN (probe A2; proof record lines 136-138). Whether to relabel (adapter) or version the contract is a CP-1 / `HERMES-FACT-001` decision |
| environment (DEV versus PROD) | not representable in the frame | present on control-plane keys: contract manifest carries environment, run mode and deployed revision (`hermes_control_plane_v1.py:161-167`, `:239-251`); proof record lines 22-50 used them to prove PROD | an adapter can bind a read to the environment by reading the manifest; the frame cannot carry it today |
| timeframe grid | any whole hour for H1 and above; sub-hour on grid (`timeframe.py:148-161`) | H4 opens 22/02/06/10/14/18 UTC; D1 opens 22:00 UTC (`candle_h4_derivation_v1.py:9-11`) | HELIOS accepts; HELIOS fixtures use 00/04/08 anchors (PROVEN by reading two fixture files) |
| depth | per-package lookback | retention 35 days for M1..H4, 120 days D1; uniform provisional minimum depth 26 (`hermes_gaps_v1.py:42-43`) | at most about 150 H4 bars fit 35 days assuming the market is closed at weekends (arithmetic, assumption stated), below the 200 an `ema_200` needs, unless HERMES computes it from deeper history (UNVERIFIED, G-07) |
| instrument and timeframe spellings | `XAU_USD`, `H4`, `M5`, `M15` (also parses `4h`, `5m`, `15m`) | same canonical spellings | HSA uses `XAUUSD`, `4H`, `5M`, `15M`; mapping owner open (PID open decision 4) |

## D. Key surface the adapter would read (names only)

`hermes:candles:XAU_USD:<TF>:latest:v1`; `hermes:candles:XAU_USD:<TF>:history:v1:<open_epoch>` with index `...:history:v1:index`; `hermes:indicators:XAU_USD:<TF>:v1`; `hermes:sessions:XAU_USD:v1`; control plane `hermes:contract:manifest:v1`, `hermes:publisher:heartbeat:v1`, `hermes:health:v1`. Source: `candle_contract_v1.py:66-69`, `hermes_indicators_v1.py` (`indicator_key`), `hermes_sessions_v1.py` (`session_key`), `hermes_control_plane_v1.py:23-26`. Whether each is ACTIVE on PROD is audit A's finding; D1 indicator publication is gated by HERMES until D1 latest is green (`hermes_indicators_v1.py`, D1 gating).
