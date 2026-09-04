"""Execution blindness, enforced structurally rather than by good intentions.

The PID forbids HELIOS from knowing anything about trade entry, re-entry,
positions, position size, stop loss, take profit, trailing stops, P&L,
win/loss, brokers, accounts, orders, fills, rejections or closures. "Make this
structurally impossible, not merely absent" is the requirement, so this module
enforces it mechanically over the whole tree:

* every identifier and every non-docstring string literal in ``helios/`` and
  ``tests/`` is checked against the forbidden vocabulary;
* every fixture value is checked too, so the data cannot smuggle the concept in;
* the published output envelope is checked to carry no such field;
* the evaluation context is checked to expose no channel through which
  downstream activity could reach a strategy.

Prose is deliberately exempt — see ``tests/_scan.py`` for why. The vocabulary
itself lives in ``tests/data/forbidden_execution_vocabulary.txt``, which is
excluded from the scan because it is the definition of the boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from helios.contracts import CER_OWNED_IDENTITY_FIELDS
from helios.contracts.output import ENVELOPE_FIELDS
from tests._scan import code_tokens, data_files, python_files, strip_comment_lines

VOCABULARY_FILE = Path(__file__).resolve().parent / "data" / "forbidden_execution_vocabulary.txt"


def load_vocabulary() -> tuple[tuple[str, ...], tuple[str, ...]]:
    bare: list[str] = []
    patterns: list[str] = []
    section = None
    for line in VOCABULARY_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "[BARE]":
            section = bare
            continue
        if stripped == "[PATTERN]":
            section = patterns
            continue
        assert section is not None, "vocabulary entry outside a section"
        section.append(stripped)
    return tuple(bare), tuple(patterns)


BARE_TERMS, PATTERN_TERMS = load_vocabulary()

BARE_RE = re.compile(r"\b(" + "|".join(BARE_TERMS) + r")\b", re.IGNORECASE)
PATTERN_RE = re.compile("|".join(f"(?:{item})" for item in PATTERN_TERMS), re.IGNORECASE)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = frozenset({VOCABULARY_FILE.resolve(), Path(__file__).resolve()})


def offences(text: str) -> list[str]:
    return [match.group(0) for match in BARE_RE.finditer(text)] + [
        match.group(0) for match in PATTERN_RE.finditer(text)
    ]


def test_the_vocabulary_is_loaded_and_substantial():
    assert len(BARE_TERMS) >= 20
    assert len(PATTERN_TERMS) >= 8
    for term in ("position", "broker", "account", "pnl", "trade", "fill"):
        assert term in BARE_TERMS


def test_the_guard_would_actually_catch_a_violation():
    """A guard that cannot fail proves nothing."""
    assert offences("position_size")
    assert offences("stop_loss")
    assert offences("self.broker.submit()")
    assert offences("trade_closed")
    assert offences("re-entry")
    assert not offences("ema_50 crossed above ema_200")
    assert not offences("ordered window of market facts")


@pytest.mark.parametrize(
    "package", [REPO_ROOT / "helios", REPO_ROOT / "tests"], ids=["helios", "tests"]
)
def test_no_code_references_execution_concepts(package):
    for path in python_files(package, exclude=EXCLUDED):
        for kind, text in code_tokens(path):
            found = offences(text)
            assert not found, f"{path}: {kind} {text!r} references {found}"


def test_no_fixture_or_data_file_references_execution_concepts():
    for path in data_files(REPO_ROOT / "fixtures", exclude=EXCLUDED):
        found = offences(strip_comment_lines(path.read_text(encoding="utf-8")))
        assert not found, f"{path} references {found}"
    for path in data_files(REPO_ROOT / "config", exclude=EXCLUDED):
        found = offences(strip_comment_lines(path.read_text(encoding="utf-8")))
        assert not found, f"{path} references {found}"


def test_the_published_envelope_has_no_execution_field():
    for name in ENVELOPE_FIELDS:
        assert not offences(name), name


def test_the_published_envelope_has_no_evidence_store_field():
    """CER owns durable evidence; HELIOS must not compete with it."""
    for name in CER_OWNED_IDENTITY_FIELDS:
        assert name not in ENVELOPE_FIELDS


def test_a_strategy_is_handed_no_channel_to_downstream_state():
    """The evaluation context is the strategy's entire world."""
    import dataclasses

    from helios.protocols import EvaluationContext

    names = {field.name for field in dataclasses.fields(EvaluationContext)}
    assert names == {
        "instrument",
        "evaluated_at_utc",
        "windows",
        "parameters",
        "freshness_policy",
        "previous",
    }
    for name in names:
        assert not offences(name), name


def test_the_strategy_interface_exposes_nothing_executable():
    from helios.protocols import StrategyEvaluator

    members = [name for name in dir(StrategyEvaluator) if not name.startswith("_")]
    assert set(members) == {"identity", "required_inputs", "evaluate"}
    for name in members:
        assert not offences(name)


def test_helios_imports_nothing_that_could_reach_a_venue():
    """No network, no database, no message bus anywhere in the package."""
    import ast

    forbidden_modules = {
        "socket", "http", "urllib", "requests", "httpx", "aiohttp",
        "sqlite3", "psycopg2", "pymysql", "mysql", "sqlalchemy",
        "redis", "kafka", "pika", "boto3", "smtplib", "ftplib", "telnetlib",
    }
    for path in python_files(REPO_ROOT / "helios"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in forbidden_modules, f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root not in forbidden_modules, f"{path} imports from {node.module}"


def test_no_legacy_helios_reference_anywhere():
    """Clean-slate proof: nothing may import or point at a previous build."""
    markers = ("srv-dev", "legacy_helios", "helios_legacy", "old_helios")
    files = python_files(REPO_ROOT / "helios", exclude=EXCLUDED) + python_files(
        REPO_ROOT / "tests", exclude=EXCLUDED
    )
    for path in files:
        text = path.read_text(encoding="utf-8").lower()
        for marker in markers:
            assert marker not in text, f"{path} references {marker}"
