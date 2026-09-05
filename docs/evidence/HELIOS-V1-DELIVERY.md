# HELIOS v1 — Delivery Evidence

Forge-governed delivery of the clean-slate HELIOS v1 strategy engine
against `PID.md`. This record lives in the repository because the Forge
delivery rule is that evidence lands in Git, never only in a session
transcript.

- **PID:** `PID.md` at `c9eacb8`
- **Branch:** `wo/WO-HELIOS-V1-VERTICAL-SLICE`
- **Presentation target:** `FUNCTIONAL_ONLY` (no browser surface; the
  Auditor's real-browser gate is therefore not applicable, and was
  replaced by real Docker runtime proof)
- **Final verdict:** `PRODUCT_GREEN`, independent fresh-context Auditor,
  at `0bb9297`

## Startup gate

`./ops/pl-startup-gate /srv/HELIOS` → PASSED (PID, git repository and
GitHub remote all present) before any work began.

## Commit trail

| Commit | Work item | Suite |
|---|---|---|
| `f0400b6` | Contract kernel: HERMES input, normalised output, state model, HSA package format | 322 |
| `1a62365` | Composition engine: `ALL`/`ANY`/`SEQUENCE`/`CONTEXT_TRIGGER` | 418 |
| `7e9a792` | HERMES / FALCON / HSA / CER boundary proofs | 658 |
| `efed87a` | Atomic strategy framework and six proof atoms | 791 |
| `650a145` | Bounded repair: determinism, transition table, guard coverage, packaging | 813 |
| `bd66904` | Runtime service, time-aligned vertical slice, Docker proven | 922 |
| `5d2e814` | One promoted identity means one definition | 922 |
| `0bb9297` | Repair of the Auditor's `PRODUCT RED` findings | 1005 |

## Delivery method

Seven bounded Engineer dispatches, each in its own isolated git worktree
cut explicitly from this repository by the PL. Because the PL session
runs from a separate Forge hub root, the bare `isolation: "worktree"`
flag would have isolated the wrong repository; every worktree was
therefore created by hand against `PROJECT_ROOT` and verified with
`git cat-file -t <known PROJECT_ROOT commit>` before an Engineer was
dispatched into it. No Engineer held commit authority at any point; the
PL reviewed and committed every diff. The shared working tree was
confirmed clean after every dispatch.

Three of the seven ran concurrently. That was justified by the product
architecture rather than for throughput: the PID requires that atomic
strategies never reference one another and that composition consume
normalised outputs, so the atoms, the chain engine and the boundary
contracts could each be built against the committed contract kernel
without depending on each other's code.

## Audit history

### First audit — `PRODUCT RED` at `5d2e814`

Three must-fix findings, each reproduced independently by the PL before
any repair was dispatched:

1. **`MarketFactWindow` was mutable and shared by reference** between
   sibling atoms. A single ordinary assignment let one strategy change
   what another strategy saw, with nothing raised, contained or logged —
   a direct breach of the PID's isolation invariant, while the code's own
   docstring claimed the object was frozen. The test written to cover
   this mutated everything *except* the one mutable object.
2. **Future-dated market facts were silently clamped** to age zero and
   published as maximally fresh.
3. **The execution-blind vocabulary guard was inert** against snake_case
   identifiers, so `broker_account` passed cleanly. Latent, with no live
   breach in the tree.

Finding 1 is the reason an independent audit exists. It survived seven
work items, a green test suite and PL review, because source inspection
and passing tests cannot detect a test that exercises the wrong object.

### Repair at `0bb9297`

All three fixed, each proven by removing the fix and watching the new
test fail. The repair's own reachability sweep found a **second instance
of finding 1's class** that the audit had not reached: `Timeframe` enum
members were mutable, and since every freshness limit derives from
`duration`, one assignment would have moved that limit for every
strategy, every later evaluation and every instrument in the process.

### Re-audit — `PRODUCT_GREEN` at `0bb9297`

A fresh Auditor, with no inherited context from the first, verified each
fix by attacking it rather than reading it: a 241-object reachability
sweep from a real `EvaluationContext` captured out of the live concurrent
evaluation path found zero remaining mutable attributes; the clock-skew
boundary was confirmed sharp to the microsecond; and the vocabulary guard
was mutation-tested across fourteen injection shapes.

Runtime was proven by the Auditor building and running its own container
from the audited commit: bare container refuses to start with exactly the
missing settings named, full run completes 61 cycles publishing 366
envelopes, restart republishes byte-identical output, health and
readiness report correctly with no host port bound, `SIGTERM` flushes the
sink and exits cleanly, and the process runs as a non-root user. The
container's published bytes hash identically to the host's, and remained
identical across varying `PYTHONHASHSEED`, `TZ`, `LC_ALL`, `PYTHONUTF8`
and a hostile ambient decimal context.

## PID acceptance criteria

| Criterion | Evidence |
|---|---|
| Clean-slate | No legacy dependency, import or reference anywhere in the tree |
| Atomic engine | Six atoms, concurrent, independently testable, isolation proven with hostile atoms through the real evaluation path |
| Chain engine | All four primitives; `SEQUENCE` proven order-sensitive; provenance preserved |
| Multi-timeframe | GOLD-style chain matched live; role→timeframe mapping declared per package; no global constant |
| Execution-blind | Structural — no channel exists on the evaluation context; mechanically guarded |
| Determinism | Byte-identical across 15 adversarial perturbations, and container output identical to host |
| Contracts | HERMES loud on malformed/stale; FALCON schema and goldens proven against real engine output; HSA package format published; CER identity compatible |
| Runtime | Docker build, start, evaluate, health, restart and logging all proven on `dell-debian` |
| Security | Public repository; gitleaks clean over the working tree and all commits; no config or secrets in source |
| Non-goals | No creep found |

## Accepted interpretations

Recorded rather than left implicit, so a later reader can revisit them:

- The PID asks for tests showing HELIOS "behaves identically regardless
  of simulated downstream execution outcomes." The build satisfies this
  with an impossibility proof — no channel to downstream execution exists
  on the evaluation context — rather than by simulating outcomes.
  Arguably stronger than the literal reading, but it is an
  interpretation.
- `PRODUCT_GREEN` in the PID is treated as equivalent to Forge's `GREEN`.

## Known follow-ups (non-blocking, from the passing audit)

Not defects against the PID, and deliberately not fixed after
`PRODUCT_GREEN` was issued rather than expanding scope past the agreed
stopping point:

1. The execution-blind data scan covers `fixtures/`, `config/` and
   `helios/` but not `docs/schema/` or `tests/data/`. Neither currently
   contains a violation and the Python loader is closed-world at every
   nesting level, so runtime cannot admit execution state — but the
   published JSON Schema is a machine-readable artefact an integrator
   targets, not prose, so the prose exemption arguably should not cover
   it.
2. Configuration is the one input surface that is not closed-world:
   unknown keys and environment variables are ignored rather than
   refused. All required and conditionally-required settings fail loudly
   by absence, so exposure is limited to genuinely optional settings
   (a typo'd optional setting silently takes its default).
3. Chain evaluation is not wrapped in the containment used for atoms.
   Fuzzing 3136 component state and direction combinations across all
   four primitives never raised, so the invariant currently holds because
   chain evaluation is total rather than because it is structurally
   contained.
4. `.gitignore` contains `lib60/`, a typo for `lib64/`.

## Integration note for HERMES

The PID states HERMES publishes at 1m/5m/15m/1h/4h/1d. HELIOS's input
contract is timeframe-generic and this build is proven on fixtures, so
nothing here is blocked. Live integration will need the M15 and H4
series to be available from HERMES, which the current HERMES schema does
not carry.
