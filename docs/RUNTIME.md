# The HELIOS runtime

How the service runs: what it does on every evaluation, how it is configured,
how to ask whether it is healthy, how it shuts down, and what it writes to the
log. `docs/DEPLOYMENT.md` covers building and running the image;
`docs/CONTRACTS.md` covers the types this document refers to.

---

## 1. What the service does

`python3 -m helios.runtime` loads its configuration, validates everything it
can before evaluating anything, and then repeats one cycle until it is told to
stop. On every evaluation instant it:

1. asks the ordered feed which facts had arrived by then;
2. records how fresh each of those facts is, **before** anything is evaluated
   against them;
3. evaluates every bound atomic strategy **concurrently**, each handed only its
   own declared window, its own declared parameters and its own last envelope;
4. composes every bound chain over those normalised envelopes;
5. publishes **every** resulting envelope — atomic and chain, matched and not —
   through the configured sink;
6. updates the status document a health probe reads.

Then it waits the configured interval and does it again.

### 1.1 What it deliberately does not do

**It holds no store.** The only state carried between evaluations is each
unit's own last published envelope, in memory — which is exactly what the
evaluation contract already defines as an input. Nothing is written anywhere
but the publication sink and the status document. CER owns durable evidence and
HELIOS must not grow a competing one.

**It adds nothing to a payload.** No publication timestamp, no sequence number,
no host identity. That is what lets two runs of one feed be compared byte for
byte, and it is why a restart republishes identical bytes (§6).

**It knows nothing about downstream activity.** There is no field, parameter or
channel through which trade, position, broker or account information could
reach a strategy. The service's entire input is market facts and definitions.

**It opens no socket.** Not for health, not for state, not for anything —
`tests/test_execution_blind.py` asserts that no module under `helios/` may even
import a networking library.

### 1.2 Ordering is fixed, not incidental

Atomic strategies are evaluated and published in canonical identity order, and
chains after them, also in canonical identity order. Concurrency never reorders
results, so a concurrent run and a sequential one publish identical bytes — and
so do two runs of the same feed.

---

## 2. The market-fact feed

HELIOS v1 ships **no live HERMES client**: no database driver, no connection
string, no credential (`docs/INTEGRATION.md` §2). Facts arrive as validated
values, and for v1 they arrive from checked-in HERMES fact documents. What
`helios/runtime/feed.py` adds is the one thing a *running* service needs and a
static fixture does not: an order in time.

The rule is entirely data-driven. Every document declares the instant its facts
should be judged against (`reference_now_utc`), and in every canonical document
the newest bar is exactly one **publication delay** past its own close. That
delay is read from the documents rather than assumed in source:

* a frame is **available** at instant `T` when `close + delay <= T`;
* the service evaluates at `close + delay` of every bar of the **finest**
  timeframe in the set, because that is the resolution at which the deployment's
  facts actually change;
* at each instant every timeframe is presented as the window of facts that had
  arrived by then, and nothing later.

So a replay never shows a strategy a fact from its own future, and two replays
of one feed are identical by construction.

The replay starts at the first instant where every timeframe can satisfy the
deepest lookback any strategy **declared** of it. That is a definition-driven
answer, not a configured one: a strategy that declared it needs 24 bars must
never evaluate against 12, and starting a replay where it would have to
manufactures a failure rather than proving anything.

A feed is refused, loudly and by name, if two documents supply one timeframe, if
a document is for another instrument, or if the documents disagree about how
long after a close their facts arrive. One feed has one cadence, and HELIOS will
not average them.

### 2.1 The shipped feed

`fixtures/hermes/scenario/` is one instrument across H4, H1, M15 and M5, all
judged against a single instant, all mutually consistent — the coarser series
are the exact aggregate of the finer wherever they overlap. See
`docs/FIXTURES.md` §5 for what it holds and what it proves.

---

## 3. Configuration

**There are no configuration values in HELIOS source.** Everything comes from
`HELIOS_*` environment variables or a TOML file named by `HELIOS_CONFIG_FILE`;
the environment wins, so one image runs in every environment and only the
injected environment differs. `helios.config.load_config()` is the single entry
point. See `config/helios.example.toml`.

### 3.1 The runtime's own settings

| environment variable | TOML | meaning |
|---|---|---|
| `HELIOS_RUNTIME_INSTRUMENT` | `[runtime] instrument` | the subject this deployment evaluates |
| `HELIOS_RUNTIME_FEED_DIR` | `[runtime] feed_dir` | directory of HERMES fact documents |
| `HELIOS_STRATEGY_PACKAGE_DIR` | `[strategies] package_dir` | directory of HSA strategy packages |
| `HELIOS_RUNTIME_TICK_SECONDS` | `[runtime] tick_seconds` | wall-clock pause between evaluations; `0` runs as fast as the feed allows |
| `HELIOS_RUNTIME_ON_FEED_END` | `[runtime] on_feed_end` | `STOP` or `REPEAT` |
| `HELIOS_RUNTIME_STATUS_FILE` | `[runtime] status_file` | where health and readiness are written |
| `HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS` | `[runtime] health_max_age_seconds` | how stale that file may be |
| `HELIOS_RUNTIME_MAX_CYCLES` | `[runtime] max_cycles` | optional bound on evaluations |

Plus everything every HELIOS process needs: `HELIOS_ENVIRONMENT`,
`HELIOS_LOG_LEVEL`, the `HELIOS_FRESHNESS_*` policy, the accepted HERMES schema
versions and the publication sink (`docs/INTEGRATION.md` §1.6).

`REPEAT` deserves a note. A v1 deployment has no live upstream, so "keep
running" can only mean "replay". A pass boundary steps evaluation time
*backwards*, and an occurrence cannot be carried across that — the contract
refuses an envelope whose match instant is later than the evaluation publishing
it, and it is right to. So the service **rearms to a cold start** at the
boundary and logs that it did, which is what makes every pass publish identical
bytes.

### 3.2 Failing loudly, once

Two checks, and both report everything wrong at once rather than one fault per
restart:

* `load_config()` validates the **shape** of everything supplied — a
  non-numeric interval, an unknown `on_feed_end`, a directory that does not
  exist — and raises `ConfigurationError` listing every problem;
* `HeliosConfig.runtime()` asserts **presence** of the settings the service
  cannot start without, and raises listing every one that is missing.

The split is deliberate: `load_config` does not demand runtime settings of a
process that is not the runtime, because a contract test or a one-off script is
a perfectly valid HELIOS process with no feed and no status file.

Startup then validates everything else that can be decided without market
facts: the strategy handoff resolving completely, every package binding to an
implementation this build ships, every chain definition being coherent, and the
feed being internally consistent. A deployment that is wrong is wrong at
startup, not three hours into a run.

### 3.3 Result codes

| code | meaning |
|---|---|
| `0` | a clean stop — the feed finished, a configured bound was reached, or a shutdown signal was honoured. The sink was flushed and closed. |
| `1` | the service started and then failed. |
| `2` | the service refused to start: configuration missing or invalid, a handoff that would not resolve, or an inconsistent feed. |

A configuration failure happens *before* logging is configured — the log level
is itself configuration — so it is written to stderr as one JSON line naming
every problem found, and then the process exits `2`.

---

## 4. Health and readiness, without a port

The PID asks for two things that look as though they pull against each other:
health and readiness must work, and there must be **no public internal state
service by default**. They only pull against each other if health has to be an
HTTP endpoint. It does not.

The service maintains one small status document. A probe reads it, judges it
against configuration, and exits `0` or `1`:

```sh
python3 -m helios.runtime.health                # readiness and liveness
python3 -m helios.runtime.health --check ready
python3 -m helios.runtime.health --check live
```

The probe reads the same configuration the service did, so a container's
`HEALTHCHECK` needs no arguments and there is no second source of truth for
where the document lives or how stale it may be.

**Ready** — the service loaded its definitions, opened its feed and completed a
full evaluation. Anything less is a process that is up and has published
nothing, which is not readiness.

**Live** — the document was updated within `health_max_age_seconds` and the
service has not stopped. A wedged evaluation loop stops updating the document
and therefore stops being live; a service that finished its feed and exited
cleanly is honestly reported as *stopped* rather than as unhealthy.

An **absent** document (the service has not started, or is pointed elsewhere)
and an **unreadable** one (something is writing over it) are distinguished from
both, and from each other, in the probe's output.

### 4.1 The status document

Written atomically — to a sibling temporary file, then renamed into place — so
a probe reading concurrently sees either the previous document or the new one
and never a half-written one.

```json
{"schema_version":"helios.runtime_status/1.0.0","phase":"READY",
 "environment":"...","instrument":"XAU_USD",
 "started_at_utc":"...Z","updated_at_utc":"...Z","last_evaluated_at_utc":"...Z",
 "cycles_completed":73,"envelopes_published":438,"contained_failures":0,
 "strategies_bound":4,"chains_bound":2,"feed_passes_completed":1,
 "publication_sink":"stream:stdout","resolution":null}
```

Phases: `STARTING`, `READY`, `STOPPING`, `STOPPED`.

It is **operational only** — counts and instants, never a verdict, a direction
or a strength. Strategy state has exactly one destination, the publication
sink, and a second differently-shaped copy of it here is how two sources of
truth are born.

---

## 5. Shutdown

`SIGTERM` and `SIGINT` are honoured as a clean shutdown: the current evaluation
finishes, the sink is flushed and closed, a final status document is written,
and the reason is logged.

```json
{"level":"INFO","message":"stop requested","stop_reason":"received SIGTERM", ...}
{"level":"INFO","message":"runtime stopped","resolution":"STOP_REQUESTED",
 "cycles_completed":73,"envelopes_published":438,"sink_flushed_and_closed":true, ...}
```

The wait between evaluations is interruptible, so a stop during a long interval
is honoured immediately rather than after it elapses. Nothing is killed
mid-payload, so a consumer reading the sink never sees half a line.

Resolutions: `FEED_EXHAUSTED`, `MAX_CYCLES_REACHED`, `STOP_REQUESTED`.

---

## 6. Restart

A restart re-reads configuration, reloads the definitions, reopens the feed and
starts again from the first instant. There is nothing to recover, because there
is nothing stored: the service derives everything from the ordered facts and
the definitions.

Which makes the restart its own proof. With a `FILE` sink, the payloads written
after a restart are **byte-identical** to the payloads written before it. If a
restart produced different bytes, something other than facts and definitions
would be feeding the result.

---

## 7. Logging

One JSON object per line, sorted keys, UTC instants ending in `Z`, on **stderr**
— so a `STREAM`/`STDOUT` publication sink and the logs are cleanly separable in
a container even though both reach `docker logs`.

Freshness is observable rather than implied. Before anything is evaluated
against a fact, the service records the judgement:

```json
{"level":"DEBUG","message":"market facts assessed","timeframe":"H4",
 "frame_timestamp_utc":"2026-03-02T04:00:00Z","evaluated_at_utc":"2026-03-02T11:01:00Z",
 "age_seconds":10860,"max_age_seconds":21660,"is_fresh":true,"is_complete":true,
 "frames_available":6,"fact_source":"hermes","fact_schema_version":"hermes.market_fact/1.0.0", ...}
```

A fact the configured policy will refuse is logged at `WARNING`, naming the age
and the limit, so an operator sees *why* a strategy refused a fact and not only
that it did. The same verdict is published in every envelope's `inputs`, so the
log and the payload cannot disagree.

Every evaluation logs what it published:

```json
{"level":"INFO","message":"evaluation complete","evaluated_at_utc":"...Z",
 "cycles_completed":37,"envelopes_published":6,"atomic_strategies_evaluated":4,
 "chains_evaluated":2,"contained_failures":0,
 "published_states":{"golden_cross":"ACTIVE","gold_continuous_sequence":"MATCHED", ...}}
```

A strategy whose evaluation raised is **contained** — it publishes an explicit
`INVALID` envelope, every sibling still publishes its own correct state — and
the containment is logged at `ERROR` with the failure type, so a contained
failure is never silent.

---

## 8. What runs, and what it publishes

The shipped deployment evaluates four atomic strategies and two chains over the
scenario feed: 61 evaluation instants, 6 envelopes each, 366 payloads per pass.
The lifecycle it produces — including the four-stage `SEQUENCE` matching at
`2026-03-02T14:01:00Z` with all four timeframes fresh — is asserted end to end
in `tests/test_vertical_slice.py` and described in `docs/FIXTURES.md` §5.

---

## 9. Where the tests are

| file | proves |
|---|---|
| `tests/test_vertical_slice.py` | the whole slice: input, atoms, chain, published contract, and byte-identical replay |
| `tests/test_runtime_service.py` | startup refusal, health and readiness, clean shutdown on `SIGTERM`, restart byte-identity, structured UTC logging |
| `tests/test_runtime_docker.py` | the image builds, starts, evaluates, reports health and restarts — and skips loudly, never vacuously, where Docker is unavailable |
| `tests/test_scenario_fixtures.py` | the feed is one instrument on one clock, mutually consistent across timeframes |
| `tests/test_scenario_packages.py` | the handoff resolves, and one promoted identity means one definition everywhere |
| `tests/test_hostile_decimal_context.py` | published bytes do not depend on the host's decimal context |
