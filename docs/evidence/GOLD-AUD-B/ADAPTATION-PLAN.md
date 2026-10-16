# Minimum adaptation plan, T2 mapping and adapter-PID input (CA item 8)

Scope guard: this is a plan of existing items and their order. It decides nothing about building the reader and prototypes none. The HELIOS-side external adapter lives outside the pure engine (CA guidance item 1; DR-34, DR-35); `HELIOS-T2` owns its implementation.

## Ordered steps

| Step | Action | Target repository | Owner persona | Depends on | Closing proof | Maps to |
|---|---|---|---|---|---|---|
| 1 | Reconcile audit A (PROD facts) with this audit's mapping and gap list | GOAL | ROGUE / PO | audit A accepted | reconciliation note | audits A and B sequence |
| 2 | Rule on fact identity: schema/version naming, spelling canon, which facts exist, indicator methods, environment discriminator (G-01, G-04, G-05, G-08, G-09, G-10) | GOAL and hermes | PO / CA with HERMES owner | 1 | recorded decisions; `HERMES-FACT-001` PID | `HERMES-FACT-001`, `GOAL-CONTRACT-001` (CP-1) |
| 3 | HERMES closes publication gaps that belong to it (status/gap accounting carried, H4 `ema_200` depth, indicator history activation: G-06, G-07, G-20) | hermes | Helm | 2 | publication evidence | `HERMES-FACT-001`, `HERMES-CUR-001` |
| 4 | HELIOS-T1 contract refactor that the adapter will target (strategy contract, package emitter question G-15) | HELIOS, HSA | HELIOS architect | `GOAL-CORE-001` | HELIOS-T1 closure | `HELIOS-T1` |
| 5 | Author the adapter PID: external adapter package outside the pure engine; GET-only reads of the surface in CONTRACT-MAPPING D; exact-decimal parsing; translation table; environment binding from the control plane; refusal of non-OK status; service live-source seam (G-14); replay determinism (G-11) | HELIOS | HELIOS architect | 2, 4 | approved PID | `HELIOS-T2` |
| 6 | Implement and test the adapter with golden fixtures taken from the probe's accept/refuse cases | HELIOS | engineer under FORGE | 5 | test suite plus independent audit | `HELIOS-T2` |
| 7 | Deployment input source is replaced by the adapter (G-12, reported privately) | a private strategy runtime repository | Helm | 6 | deployment record | deployment item |

## Which HELIOS-T2 requirements the findings satisfy, contradict, or leave missing

HELIOS-T2 is "Live HERMES adapter" in the backlog at the PID base commit (`depends_on`: `HELIOS-T1`, `HERMES-FACT-001`; no PID).
- Satisfied by existing artefacts: acceptance boundary (`accept_market_facts`), freshness policy, closed-world validation, deterministic replay feed, publication sinks.
- Contradicted: the PID's earlier wording "HERMES/ARES adapters" contradicts the backlog title now in force; this audit maps the HERMES half only. A Redis publication sink is not required by any finding (a private runtime design record defers it).
- Missing: every step above numbered 2 to 6; in particular no reader, no translation, no environment binding, no live-source seam.

## Input for the adapter PID (CA guidance item 1)

- Extends: no existing HELIOS artefact. Reference material (not for cross-repository import): the refusal discipline of the CERBERUS reader (INVENTORY C12) and the index-plus-batched-read history pattern of the ARES fetch (C13). The HELIOS boundary module is the acceptance path the adapter's output must pass unchanged.
- Must translate: the field table in CONTRACT-MAPPING A and B, the version/environment items in C, and refuse everything in the status table of G-06.
- Must not: add Redis or network imports to the pure engine; compute indicators; relabel DEV as PROD; default a missing fact.
