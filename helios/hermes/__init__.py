"""HERMES-facing adapters.

HELIOS consumes HERMES facts; it does not produce them. WI-1 ships one offline
adapter — the fixture source — so that contracts, freshness rules and later
strategy work items can be proven end to end without a live feed. There is no
database client, no credential and no connection string in this package.
"""

from helios.hermes.fixture_source import (
    FIXTURE_SCHEMA_VERSION,
    HermesFixture,
    load_fixture,
    load_fixture_frames,
    read_fixture_document,
)

__all__ = [
    "FIXTURE_SCHEMA_VERSION",
    "HermesFixture",
    "load_fixture",
    "load_fixture_frames",
    "read_fixture_document",
]
