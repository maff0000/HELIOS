# HELIOS

The deterministic, execution-blind strategy-state engine for the GOLD trading
stack.

HELIOS consumes HERMES market facts, continuously evaluates independent atomic
strategies and governed strategy chains, and publishes normalised strategy and
chain state for FALCON. Given identical ordered facts, identical strategy
definitions and an identical code version, it produces identical state —
regardless of what any downstream system does or does not do.

Forge-governed. The authoritative product contract is [`PID.md`](PID.md).

## Execution blindness

HELIOS has no concept of trade entry, position, stop loss, take profit,
trailing stop, P&L, win/loss, broker, account, order, fill or closure. This is
enforced mechanically, not by convention: `tests/test_execution_blind.py` scans
every identifier, literal and fixture value in the tree against
`tests/data/forbidden_execution_vocabulary.txt`, checks the published envelope
carries no such field, and checks the evaluation context exposes no channel
through which downstream activity could reach a strategy.

## Current state

Built and tested:

* the contract kernel — HERMES-compatible market-fact input with explicit
  freshness and validity rules, the strategy state model and its legal
  transitions, the normalised output contract FALCON consumes with
  deterministic JSON, CER-compatible identity and version immutability;
* the HSA-targetable strategy package format, its loader and its JSON Schema;
* the atomic strategy framework and six proof atoms;
* the composition engine — `ALL`, `ANY`, `SEQUENCE` and `CONTEXT_TRIGGER`
  chains over normalised component output, with provenance preserved;
* the FALCON publication boundary — canonical JSON lines to a file, a stream or
  memory, with schema-version negotiation;
* the HERMES, HSA and CER boundary contracts, with fixtures for each;
* external configuration and structured UTC logging;
* the Docker-first runtime — an ordered market-fact feed, continuous concurrent
  evaluation, health and readiness with no listening socket, clean shutdown and
  restart, and a time-aligned scenario that drives the whole vertical slice.

Absent by design: NEO decision logic, TRON execution, account risk, broker
integration, backtesting, strategy auto-tuning, a dashboard, an evidence store
(CER owns that) and any reusable indicator library (HERMES owns indicators).

## Getting started

```sh
./ops/bootstrap                     # arms the gitleaks pre-commit gate
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Requires Python 3.11+ and [gitleaks](https://github.com/gitleaks/gitleaks).

To run the service itself:

```sh
docker build -t helios:dev .
docker run -d --name helios --env-file <your settings> helios:dev
docker exec helios python3 -m helios.runtime.health
```

`config/helios.example.toml` lists every setting; nothing is defaulted in
source. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

**Run `./ops/bootstrap` before your first commit.** This repository is public;
the pre-commit hook scans staged content for secrets and refuses the commit if
it finds any.

## Configuration

There are no configuration values in source. Supply `HELIOS_*` environment
variables or point `HELIOS_CONFIG_FILE` at a TOML file; the environment wins
over the file. `helios.config.load_config()` is the single entry point: it
validates everything at startup — freshness, logging, accepted upstream schema
versions and the publication destination — and reports every problem it found
in one error. See [`config/helios.example.toml`](config/helios.example.toml).

## Documentation

* [`docs/CONTRACTS.md`](docs/CONTRACTS.md) — the contract reference: input
  contract, state model and legal transitions, output contract, identity
  semantics, strategy package format.
* [`docs/INTEGRATION.md`](docs/INTEGRATION.md) — the integration reference for
  FALCON, HERMES, HSA and CER: what HELIOS publishes, what it consumes, how a
  handoff resolves, and where the bytes go.
* [`docs/ATOMS.md`](docs/ATOMS.md) — the atomic strategy framework, the six
  proof atoms and how failure is contained.
* [`docs/COMPOSITION.md`](docs/COMPOSITION.md) — the chain engine: the four
  primitives, direction and timing semantics, and chain provenance.
* [`docs/FIXTURES.md`](docs/FIXTURES.md) — every checked-in fixture set and what
  each one proves, including the time-aligned scenario the runtime evaluates.
* [`docs/RUNTIME.md`](docs/RUNTIME.md) — the running service: the evaluation
  loop, the ordered feed, configuration, health and readiness, shutdown,
  restart and logging.
* [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — building and running the image,
  and what differs when the same proven image is promoted.
* [`docs/schema/strategy_package.schema.json`](docs/schema/strategy_package.schema.json)
  — JSON Schema for strategy packages, generated from the model and
  drift-checked by a test.
* [`docs/schema/strategy_state.schema.json`](docs/schema/strategy_state.schema.json)
  — JSON Schema for the published envelope, generated the same way.
