# HELIOS ← HERMES PROD Integration Proof 0001

**Terminal verdict: `WAITING_FOR_HERMES_BACKFILL`**

Work order `WO-HELIOS-HERMES-PROD-INTEGRATION-PROOF-0001`. This record is
committed because the finding is an escalation to central architecture,
and a finding that lives only in a session transcript is not durable
truth.

**No HELIOS product source was changed. No integration harness was
built.** The work order's precondition gate (Phase 0) did not pass, and
the gate exists precisely so that no harness is built against an
unproven or incomplete source.

- **HELIOS base SHA:** `7f7193d55475e40583892b94dd461b1b64d6f1ef` (`main`,
  the merged, independently audited v1 boundary)
- **HELIOS product code changed:** `NO`
- **All observations UTC.** Verification performed 2026-09-05, a
  **Saturday**, with the gold market closed — which is material to the
  findings below.

## Phase 0A — endpoint identity: PASSED (PROD proven)

HERMES PROD was reached and positively proven to be PROD, distinct from
HERMES DEV.

The endpoint address is deliberately **not recorded here** — this
repository is public. It is declared in the ARES production manifest on
the estate. Identity is instead proven by governed fields read from the
endpoint itself:

| Signal | PROD endpoint | DEV endpoint (dell-debian) |
|---|---|---|
| `contract:manifest:v1` `environment` | `PROD` | — |
| `run_env` | `PRODUCTION` | `STAGING` |
| `redis_target` | `hermes-cache:6379/db0` | `<dev host>:6379/db0` |
| Namespace | `hermes:` only | shared platform bus |

Two independent signals, as required:

1. **The surface self-declares PROD through a fail-closed gate.** HERMES
   defines `RUN_ENV_BY_ENVIRONMENT = {"DEV": "STAGING", "PROD":
   "PRODUCTION"}`; a mismatched pair raises `IDENT-RUNENV-PAIR-MISMATCH`
   and startup aborts. A DEV writer structurally cannot publish
   `PROD`/`PRODUCTION`.
2. **Topology matches the canonical PROD architecture and is impossible
   on the dev host.** The PROD heartbeat reports `redis_target =
   hermes-cache:6379/db0`, matching the documented standalone
   three-container PROD stack. No `hermes-cache` container exists on the
   dev host.

### Correction to an earlier PL finding

The PL initially returned `BLOCKED_HERMES_PROD_ENDPOINT`. **That was
wrong**, and an independent Auditor overturned it. Two PL findings were
false:

- The PL inspected *candle* envelopes, found no environment marker, and
  generalised to the whole surface. The marker exists on
  `contract:manifest:v1` and `publisher:heartbeat:v1` — a different key
  family on the same surface.
- The PL searched HERMES's own deploy tooling and host network artefacts
  for a PROD host, but not the consumer estate, where the endpoint is
  declared.

Recorded because the failure mode is reusable: a negative was asserted
from a single sampled key family rather than from the whole contract
surface.

## Phase 0B — six canonical XAU_USD timeframes: FAILED

Observed on the PROD endpoint, 2026-09-05T15:47Z (Saturday, market
closed):

| TF | history keys | `latest:v1` | oldest | newest | age at observation |
|---|---|---|---|---|---|
| M1 | 22,823 | absent | 2026-08-11T08:25Z | 2026-09-05T14:05Z | 1h 42m |
| M5 | 6,357 | absent | 2026-08-11T08:25Z | 2026-09-05T15:35Z | 12m |
| M15 | 2,120 | present | 2026-08-11T08:30Z | 2026-09-05T15:15Z | 32m |
| H1 | 547 | present | 2026-08-11T09:00Z | 2026-09-05T14:00Z | 1h 47m |
| **H4** | 134 | **absent** | 2026-08-11T18:00Z | **2026-09-03T10:00Z** | **2d 5h** |
| **D1** | 21 | **absent** | 2026-08-11T22:00Z | **2026-09-01T22:00Z** | **3d 17h** |

Publisher state at observation: `status = WARN`, `d1_state =
PENDING_FIRST_DAILY_SEAL`.

### Blocking gaps

**D1 is incomplete — the decisive finding.** 21 history bars against the
contract's own `min_required_depth: 26`; no `D1:latest:v1` key; the
publisher itself reports `PENDING_FIRST_DAILY_SEAL`; newest bar is
Tuesday 2026-09-01, 3d 17h stale. HERMES has not yet sealed a first
daily candle on PROD.

**H4 is also incomplete**, which was not in the original brief. Its
newest bar is Thursday 2026-09-03T10:00Z, so the whole of Friday's
trading session is absent, and it has no `latest:v1` key.

### Internal inconsistency worth architectural attention

The intraday series (M1/M5/M15/H1) advanced **into Saturday, with the
gold market closed** — M5's newest bar is timestamped 12 minutes before
observation — while H4 and D1, which derive from that same feed, are
stale by days.

A feed that is simultaneously publishing current bars and failing to
seal the higher timeframes those bars roll up into is internally
inconsistent. Either the Saturday intraday bars are not genuine market
facts (carry-forward or seed replay), or the H4/D1 sealing path is
broken — and the two readings have different owners. This is stated as
an observation, not a diagnosis; it was not diagnosed here because
diagnosing HERMES is outside this work order's authority.

Note the DEV endpoint behaves *correctly* on this point, stopping at
Friday's close (2026-09-04T20:58Z), and carries deeper D1 history (82
bars) than PROD.

## Phases 1–9 — not executed

The Phase 0 gate did not pass, so no contract observation, harness,
ingestion, atomic/chain proof, replay or failure-mode work was performed.
This is the gate behaving as designed: it exists to prevent a harness
being built against an incomplete source, and to prevent HERMES DEV
being silently substituted when PROD proves unusable. The DEV endpoint
carries well-formed, currently *better*-populated data, which is exactly
why substituting it would have been easy and wrong.

## Known HELIOS-side gap (recorded, not acted on)

Ahead of any future attempt, and not addressed here because it would
require product change:

- HELIOS v1 has **no Redis client**. Its published configuration states
  there is deliberately no network sink and no state service; sinks are
  stream, file and memory only.
- HELIOS validates market facts at `hermes.market_fact/1.0.0`, whereas
  the live HERMES contract publishes `schema_version: v1`, `contract:
  hermes.candles.latest`.

Whether a bounded harness may map that as transport, or whether it
constitutes reinterpreting semantics across an ownership boundary, is an
open architecture question. It is **not** resolved here, and under the
work order it may not be resolved by implementation.

## Escalation to central architecture

1. **HERMES PROD D1 backfill/seal is incomplete** (21 of 26 required
   bars, no daily seal). Owner: HERMES.
2. **HERMES PROD H4 is stale**, missing an entire trading session.
   Owner: HERMES.
3. **Saturday intraday publication into a closed market**, alongside
   stale higher timeframes. Owner: HERMES. Needs diagnosis before any
   such bar is treated as market fact by any consumer.
4. **HELIOS↔HERMES contract-version reconciliation**
   (`hermes.market_fact/1.0.0` vs `v1`/`hermes.candles.latest`), and
   whether HELIOS acquires a Redis reader at all. Cross-boundary; needs
   a ruling before a versioned change is opened.

## Re-run precondition

This work order becomes executable when HERMES PROD reports `D1`
`latest:v1` present with `d1_state` no longer
`PENDING_FIRST_DAILY_SEAL` and at least `min_required_depth` bars, and
`H4` is current through the most recent closed session. Item 4 above
should be ruled on before, not during, that re-run.
