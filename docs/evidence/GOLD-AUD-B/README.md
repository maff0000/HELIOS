# GOLD-AUD-B evidence record

Audit id: `GOLD-AUD-B` (read-only). Lane: research. Primary evidence repository: `maff0000/HELIOS`, branch `wo/GOLD-AUD-B-evidence`, path `docs/evidence/GOLD-AUD-B/`.
Independent audit verdict (CA item 10): **pending independent audit by ROGUE (AUD-2)**. This record does not self-certify.

## CA item 1: PID / WO identifier and exact audited scope

PID: `GOLD-AUD-B` (GOAL `pids/GOLD-AUD-B.md`, GOAL main `ceaf0291a43d029b1a47b1c192a62ff147f05ac0`, approved by Central Architecture 15 and 16 October 2026).
Quoted scope sentence (PORTFOLIO.md section 2, row GOLD-AUD-B): "Search the recorded locations for any previously completed HERMES Redis to HELIOS GOLD integration (external adapter, deployment wiring, legacy), distinguish legacy HELIOS generations, the newer deterministic engine and the deployed stack, reconcile what is found with the new HELIOS runtime and the MarketFactFrame, recommend minimum reuse or adaptation for the HELIOS-side external adapter, and tabulate HSA candidate requirements against the PROD facts of audit A (no ranking, no selection)."
Added scope (Central Architecture directive 16 Oct 2026, relayed by ROGUE): section "Reusable atoms and strategy package model" ([STRATEGY-MODEL.md](STRATEGY-MODEL.md)), including the expressibility question for the design candidate `GOLD-TREND-PO3-001`. That candidate is a DESIGN CANDIDATE only: not selected, implemented, qualified or promoted. No contract is frozen. The re-engineered HELIOS is preserved; nothing here authorises replacing it.

## CA item 2: source repositories and revisions (full SHA)

| Repository | Visibility | Revision audited | Used for |
|---|---|---|---|
| maff0000/HELIOS | public | `7f7193d55475e40583892b94dd461b1b64d6f1ef` (main at dispatch); branch `wo/WO-HELIOS-HERMES-PROD-INTEGRATION-PROOF-0001` at `84d3772a91b0dbf6b9d2006a20efdaa0ecca0326` | newer deterministic engine, proof branch |
| maff0000/hermes | public | `ca1901789f8c7a7e085d02ebde8f7264d90d669e` | live publication contracts (source) |
| a private strategy runtime repository | private | revision recorded privately | deployment wiring (summarised only) |
| maff0000/HSA | public | `279ec181b5cd78df953671e087e01b7c1190ff96` | candidate strategy packages, package schema |
| maff0000/falcon | public | `8f218ec1b85b932285b042a7b1ae64dbd11eebee` | independent HELIOS discovery record |
| maff0000/cerberus | public | `ffe85e6c9400a2c296b876419b095beeff0f3f96` | an existing, separate HERMES Redis candle reader |
| a private ARES repository | private | revision recorded privately | an existing, separate HERMES Redis candle reader (summarised only) |
| maff0000/GOAL | public | `ceaf0291a43d029b1a47b1c192a62ff147f05ac0` (PID base); candidate PID branches read for `goal.strategy/v1` | backlog, PID, registers |
| legacy HELIOS lineage | private | revisions recorded privately | legacy generations (summarised only) |

## Document index (CA ten mandatory items)

| CA # | Item | Where |
|---|---|---|
| 1 | scope | this file |
| 2 | sources, commands, negative-search record | this file, [SEARCH-LOG.md](SEARCH-LOG.md) |
| 3 | existing implementation | [INVENTORY.md](INVENTORY.md) |
| 4 | actual versus expected contract | [CONTRACT-MAPPING.md](CONTRACT-MAPPING.md) |
| 5 | tests, fixtures, evidence | [TESTS-AND-REPRODUCTION.md](TESTS-AND-REPRODUCTION.md) |
| 6 | defects and compatibility gaps | [GAPS.md](GAPS.md) |
| 7 | reusable code and documentation | [INVENTORY.md](INVENTORY.md), [DISPOSITION.md](DISPOSITION.md), [CANDIDATE-REQUIREMENTS.md](CANDIDATE-REQUIREMENTS.md), [STRATEGY-MODEL.md](STRATEGY-MODEL.md) |
| 8 | minimum recommended changes | [ADAPTATION-PLAN.md](ADAPTATION-PLAN.md) |
| 9 | safety and deployment constraints | [SAFETY.md](SAFETY.md) |
| 10 | independent audit verdict | pending independent audit by ROGUE |

## Headline findings (labels: PROVEN / PARTIAL / UNVERIFIED / MISSING)

1. **No completed HERMES Redis to new-HELIOS integration was found in the searched locations (PROVEN for the locations in SEARCH-LOG.md; absence elsewhere is UNVERIFIED).** The newer HELIOS has no Redis client by construction (imports are refused by test). Every candidate located is a database reader, a fixture-directory feed, a contract-only skeleton, an unmerged proof record, or a read-only probe.
2. **Findings about the input of the currently deployed stack were reported privately (G-12).** This record makes no public statement about its content.
3. **Two separate, working HERMES Redis candle readers exist outside HELIOS** (CERBERUS: single latest key, verified live per its status record; ARES: latest plus history index). They are reference material for the adapter, not HELIOS integrations (PROVEN by source reading; live behaviour UNVERIFIED here).
4. **Live HERMES output does not pass HELIOS validators untranslated (PROVEN, reproduced):** five of six raw-form probes refused; accepted only after exact-decimal parsing, schema-version relabel, candle field rename, uppercase session, indicator allow-listing.
5. **The 4-stage H4/H1/M15/M5 SEQUENCE skeleton is expressible in HELIOS v1 as shipped, but the proposed Trend-Pullback-PO3 content is not expressible with the six shipped atoms** (see STRATEGY-MODEL.md).
6. Neither HSA candidate package is loadable by HELIOS as-is; no HSA-to-HELIOS package emitter was found in HSA main.

Audit A (PROD facts) output was not available to this audit; every statement about live PROD content is UNVERIFIED except the dated observations quoted from earlier records.
