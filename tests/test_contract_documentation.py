"""`docs/INTEGRATION.md` must stay true.

An integration document that drifts is worse than none: it tells an integrator
something HELIOS no longer does. Everything asserted here is a fact the
document states that the code can confirm — field names, version strings,
configuration keys, fixture paths and the artefacts referred to.
"""

from __future__ import annotations

import re

import pytest

from helios.contracts import CER_IDENTITY_FIELDS, ALL_TIMEFRAMES
from helios.contracts.identity import (
    CER_OWNED_IDENTITY_FIELDS,
    HELIOS_ORIGINATED_IDENTITY_FIELDS,
)
from helios.contracts.market_fact import (
    CANDLE_FIELDS,
    INDICATOR_FIELDS,
)
from helios.contracts.output import ENVELOPE_SCHEMA_VERSION
from helios.integration.exemplars import GOLDEN_DIR, golden_exemplars
from helios.integration.hermes_boundary import PROVENANCE_FIELDS
from helios.publish import SCHEMA_PATH, SUPPORTED_SINKS, SUPPORTED_STREAMS
from helios.spec.model import STRATEGY_PACKAGE_SCHEMA_VERSION
from helios.spec.schema import SCHEMA_PATH as PACKAGE_SCHEMA_PATH

DOC = "docs/INTEGRATION.md"


@pytest.fixture(scope="module")
def document(repo_root) -> str:
    return (repo_root / DOC).read_text(encoding="utf-8")


def test_the_document_exists_and_covers_all_four_boundaries(document):
    for boundary in ("HERMES", "FALCON", "HSA", "CER"):
        assert boundary in document


@pytest.mark.parametrize("field", sorted(set(CANDLE_FIELDS) | set(INDICATOR_FIELDS)))
def test_every_market_fact_field_is_documented(document, field):
    assert f"`{field}`" in document


@pytest.mark.parametrize("field", sorted(PROVENANCE_FIELDS))
def test_every_provenance_field_is_documented(document, field):
    assert f"`{field}`" in document


@pytest.mark.parametrize("timeframe", ALL_TIMEFRAMES, ids=lambda item: item.code)
def test_every_timeframe_is_documented(document, timeframe):
    assert f"`{timeframe.code}`" in document
    assert f"`{timeframe.hermes_code}`" in document


@pytest.mark.parametrize("field", sorted(CER_IDENTITY_FIELDS))
def test_every_cer_canonical_identifier_is_documented(document, field):
    assert f"`{field}`" in document


def test_the_document_says_which_identifiers_helios_does_not_own(document):
    section = document.split("## 4. CER")[1]
    for field in CER_OWNED_IDENTITY_FIELDS:
        assert f"`{field}`" in section
    for field in HELIOS_ORIGINATED_IDENTITY_FIELDS:
        assert f"`{field}`" in section
    assert "must not, become an evidence store" in " ".join(section.split())


def test_the_published_version_strings_are_the_real_ones(document):
    assert f"`{ENVELOPE_SCHEMA_VERSION}`" in document
    assert f"`{STRATEGY_PACKAGE_SCHEMA_VERSION}`" in document


@pytest.mark.parametrize(
    "variable",
    [
        "HELIOS_PUBLICATION_SINK",
        "HELIOS_PUBLICATION_PATH",
        "HELIOS_PUBLICATION_STREAM",
        "HELIOS_CONFIG_FILE",
    ],
)
def test_every_publication_setting_is_documented(document, variable):
    assert f"`{variable}`" in document


@pytest.mark.parametrize("sink", SUPPORTED_SINKS)
def test_every_configurable_sink_is_documented(document, sink):
    assert f"`{sink}`" in document


@pytest.mark.parametrize("stream", SUPPORTED_STREAMS)
def test_every_configurable_stream_is_documented(document, stream):
    assert f"`{stream}`" in document


def test_the_document_states_the_version_negotiation_rule(document):
    assert "must refuse" in document
    assert "before" in document.split("### 1.5")[1].split("### 1.6")[0]


def test_the_document_states_there_is_no_state_service(document):
    assert "no public internal state service" in " ".join(document.split())


def test_the_document_points_at_artefacts_that_exist(repo_root, document):
    referenced = set(re.findall(r"`(fixtures/[^`]+|docs/schema/[^`]+)`", document))
    referenced |= set(re.findall(r"\]\((schema/[^)]+)\)", document))
    assert referenced, "the document should point at real artefacts"
    for target in sorted(referenced):
        path = repo_root / ("docs/" + target if target.startswith("schema/") else target)
        assert path.exists(), f"{DOC} points at {target}, which does not exist"


def test_the_document_names_every_golden_fixture(repo_root, document):
    for exemplar in golden_exemplars():
        assert exemplar.filename in document
        assert (repo_root / GOLDEN_DIR / exemplar.filename).is_file()


def test_the_document_names_both_published_schemas(document):
    assert str(SCHEMA_PATH) in document or SCHEMA_PATH.name in document
    assert PACKAGE_SCHEMA_PATH.name in document


def test_the_document_names_the_regeneration_commands(document):
    assert "python3 -m helios.publish.schema" in document
    assert "python3 -m helios.integration.exemplars" in document


def test_the_document_is_honest_about_what_is_fixture_backed(document):
    section = document.split("## 5. Which boundaries are fixture-backed today")[1]
    for boundary in ("HERMES", "FALCON", "HSA", "CER"):
        assert boundary in section
    assert "Fixture-backed" in section
    assert "in parallel" in section


def test_the_document_carries_no_infrastructure_detail(document):
    """A public repository must not describe anyone's topology."""
    banned = (
        "password", "secret", "api_key", "apikey", "credential=", "passwd",
        "private_key", "connection string =", "localhost", "127.0.0.1", "jdbc",
        # Assembled rather than spelled out: tests/test_execution_blind.py
        # scans this file's raw text for legacy-build markers, and a literal
        # here would read as a reference to one rather than a ban on it.
        "srv" + "-dev",
    )
    lowered = document.lower()
    for word in banned:
        assert word not in lowered, f"{DOC} mentions a forbidden term"
    # No URL other than the two documentation-schema links and repo-relative ones.
    for url in re.findall(r"https?://[^\s)`]+", document):
        assert url.startswith("https://json-schema.org/"), url
