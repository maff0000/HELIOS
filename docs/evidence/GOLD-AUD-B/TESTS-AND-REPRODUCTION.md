# Tests, fixtures and reproduction (CA item 5)

## 1. HELIOS own tests, unchanged

Clean fresh checkout of HELIOS at `7f7193d55475e40583892b94dd461b1b64d6f1ef`; command `python3 -m pytest -p no:cacheprovider -q` with the container tool deliberately hidden from the path; result **1000 passed, 5 skipped** (1005 collected, equal to the PID baseline of 1005). The five skipped tests are the Docker runtime proofs in `tests/test_runtime_docker.py` (lines 196, 214, 270, 311, 357); they build and run an image and one of them uses `docker exec`, which this audit's rules forbid, so they were not run (the skip reason names this). `git status` after the run: clean; no test or source file modified. The same run is repeated after the evidence files were added; see the final line of SAFETY.md. The acceptance wording "same pass count" is therefore met as 1000 + 5 skipped = 1005, not as 1005 passed (a deviation, disclosed). HSA's own suite (871 tests) was also run in its clean clone: 871 passed.

## 2. Probe reproduction in a throwaway no-network container

Environment: a throwaway container started with networking disabled, a read-only root, all capabilities dropped, run once to completion and removed; the HELIOS tree mounted read-only; the image was an existing locally built image used only for its Python and validator dependencies (HELIOS code came from the mounted audited tree). Inputs: one private captured sample (three objects: candle, indicators, session; XAU_USD H1 bar opening 2026-10-07T07:00Z). Output captured privately (checksum in SAFETY.md).

| Case | Treatment | Result |
|---|---|---|
| A1 | candle prices as binary floats | REFUSE: binary float rejected |
| A2 | schema version exactly `v1` | REFUSE: schema version not `<namespace>/<major.minor.patch>` |
| A3 | session lowercase as published | REFUSE: session pattern requires uppercase |
| A4 | HERMES field `is_closed` instead of `complete` | REFUSE: `complete` required, `is_closed` forbidden |
| A5 | extra indicators passed through | REFUSE: unknown indicator fields forbidden |
| A6 | millisecond timestamp as published | PASS |
| B1 | exact decimals, relabelled schema version, six HELIOS indicators only | PASS; frame key `(XAU_USD, H1, 2026-10-07T07:00:00+00:00)`, close time 08:00 |
| B2 | B1 plus session uppercased | PASS |
| C | the six HELIOS indicator names present in the HERMES indicator object | all six present (sample is H1 only) |

This reproduces the probe's accept and refuse results (CA item 5). It proves translation is required and sufficient for that one bar; it does not prove PROD completeness (audit A).

## 3. HSA package loaded through HELIOS

Both HSA candidate packages (`strategies/gold_context_breakout/1.0.0/package.json`, `strategies/wick_rejection_sequence/0.1.0/package.json`) were passed to HELIOS `parse_strategy_package` in the same kind of no-network container. Both: REFUSE (missing `evaluate_on`, extra `evaluation_timeframe` and further extra top-level fields). The two schemas share almost no top-level fields: HELIOS requires `direction expiry identity inputs kind metadata parameters persistence schema_version timing`; HSA requires a 29-field set including `required_hermes_fields`, `atomic_strategies`, `deterministic_test_cases`. A grep of HSA main for the HELIOS package schema id or `evaluate_on` returns nothing: **no HSA emitter of HELIOS-format packages was found (MISSING)**.

## 4. Fixtures relevant to a GOLD run (HELIOS)

HERMES-style facts: `fixtures/hermes/{xau_usd,scenario,stale,malformed}/` (12 malformed cases cover closed-world, ordering, alignment, schema); packages: `fixtures/strategy_packages/{valid,scenario,malformed}/`; HSA handoff fixtures `fixtures/hsa/{handoff,underspecified,unresolved}/`. All fact fixtures declare source `hermes` and schema `hermes.market_fact/1.0.0` (PROVEN by reading two files and the loader).
