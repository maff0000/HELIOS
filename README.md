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

## Current state — WI-1: contracts and domain kernel

This work item defines the vocabulary every later work item consumes:

* HERMES-compatible market-fact input contract, with explicit freshness and
  validity rules;
* the strategy state model and its legal transitions;
* the normalised output contract FALCON consumes, with deterministic JSON;
* CER-compatible strategy identity and version immutability;
* the HSA-targetable strategy package format, its loader and its JSON Schema;
* canonical HERMES fixtures, including deliberately malformed and stale ones;
* external configuration and structured UTC logging.

The atomic strategies, the composition engine and the Docker runtime are
separate work items.

## Getting started

```sh
./ops/bootstrap                     # arms the gitleaks pre-commit gate
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Requires Python 3.11+ and [gitleaks](https://github.com/gitleaks/gitleaks).

**Run `./ops/bootstrap` before your first commit.** This repository is public;
the pre-commit hook scans staged content for secrets and refuses the commit if
it finds any.

## Configuration

There are no configuration values in source. Supply `HELIOS_*` environment
variables or point `HELIOS_CONFIG_FILE` at a TOML file; the environment wins
over the file. Configuration is validated at startup and every problem is
reported at once. See [`config/helios.example.toml`](config/helios.example.toml).

## Documentation

* [`docs/CONTRACTS.md`](docs/CONTRACTS.md) — the integration reference for HSA,
  FALCON and CER: input contract, state model, output contract, identity
  semantics, strategy package format.
* [`docs/FIXTURES.md`](docs/FIXTURES.md) — the fixture format and what each
  fixture proves.
* [`docs/schema/strategy_package.schema.json`](docs/schema/strategy_package.schema.json)
  — JSON Schema for strategy packages, generated from the model and
  drift-checked by a test.
