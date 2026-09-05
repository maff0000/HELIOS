"""CER-compatible identity: validation and immutability."""

from __future__ import annotations

import pytest

from helios.contracts import (
    CER_IDENTITY_FIELDS,
    CER_OWNED_IDENTITY_FIELDS,
    ChainId,
    ChainIdentity,
    StrategyId,
    StrategyIdentity,
    StrategyVersion,
)
from helios.contracts.identity import (
    HELIOS_ORIGINATED_IDENTITY_FIELDS,
    assert_version_immutable,
)
from helios.errors import IdentityError


def test_cer_identity_vocabulary_is_complete_and_partitioned():
    assert set(CER_IDENTITY_FIELDS) == {
        "strategy_id", "strategy_version", "experiment_id",
        "run_id", "evidence_id", "artifact_id",
    }
    assert set(HELIOS_ORIGINATED_IDENTITY_FIELDS) == {"strategy_id", "strategy_version"}
    assert set(CER_OWNED_IDENTITY_FIELDS) == set(CER_IDENTITY_FIELDS) - set(
        HELIOS_ORIGINATED_IDENTITY_FIELDS
    )


@pytest.mark.parametrize("value", ["golden_cross", "range_breakout", "swing_proximity_h1"])
def test_valid_strategy_ids(value):
    assert str(StrategyId(value)) == value


@pytest.mark.parametrize("value", ["Golden_Cross", "gc", "9cross", "golden-cross", "", "a b"])
def test_malformed_strategy_ids_fail_loudly(value):
    with pytest.raises(IdentityError):
        StrategyId(value)


def test_a_chain_id_cannot_masquerade_as_a_strategy_id():
    """Structural distinction, not merely a naming convention."""
    with pytest.raises(IdentityError):
        StrategyId._coerce(ChainId("gold_context_trigger"))


@pytest.mark.parametrize("value", ["1.0.0", "0.0.1", "12.34.56"])
def test_valid_versions(value):
    assert str(StrategyVersion.parse(value)) == value


@pytest.mark.parametrize("value", ["1.0", "01.0.0", "1.0.0-rc1", "v1.0.0", "1.0.0.0", 1, None])
def test_malformed_versions_fail_loudly(value):
    with pytest.raises(IdentityError):
        StrategyVersion.parse(value)


def test_versions_order_naturally():
    assert StrategyVersion.parse("1.0.0") < StrategyVersion.parse("1.0.1")
    assert StrategyVersion.parse("1.9.0") < StrategyVersion.parse("1.10.0")
    assert StrategyVersion.parse("2.0.0") > StrategyVersion.parse("1.99.99")


def test_bumping_a_version_produces_a_new_value():
    version = StrategyVersion.parse("1.2.3")
    assert str(version.next_major()) == "2.0.0"
    assert str(version.next_minor()) == "1.3.0"
    assert str(version.next_patch()) == "1.2.4"
    assert str(version) == "1.2.3"


def test_identity_is_immutable():
    identity = StrategyIdentity("golden_cross", "1.0.0")
    assert identity.canonical == "golden_cross@1.0.0"
    with pytest.raises(Exception):
        identity.strategy_version = StrategyVersion.parse("2.0.0")  # type: ignore[misc]


def test_identity_round_trips_through_its_canonical_form():
    identity = StrategyIdentity.parse("range_breakout@2.1.3")
    assert identity.canonical == "range_breakout@2.1.3"
    assert StrategyIdentity.parse(identity.canonical) == identity


@pytest.mark.parametrize("value", ["golden_cross", "golden_cross@", "@1.0.0", "a@b@c", 7])
def test_malformed_canonical_identity_fails_loudly(value):
    with pytest.raises(IdentityError):
        StrategyIdentity.parse(value)


def test_a_promoted_version_cannot_be_redefined_in_place():
    promoted = StrategyIdentity("golden_cross", "1.0.0")
    with pytest.raises(IdentityError) as caught:
        assert_version_immutable(promoted, promoted, definition_changed=True)
    assert "immutable" in str(caught.value)


def test_a_logic_change_is_accepted_as_a_new_version():
    promoted = StrategyIdentity("golden_cross", "1.0.0")
    proposed = StrategyIdentity("golden_cross", "1.1.0")
    assert_version_immutable(promoted, proposed, definition_changed=True)


def test_an_unchanged_definition_must_keep_its_version():
    promoted = StrategyIdentity("golden_cross", "1.0.0")
    proposed = StrategyIdentity("golden_cross", "1.1.0")
    with pytest.raises(IdentityError):
        assert_version_immutable(promoted, proposed, definition_changed=False)


def test_a_different_strategy_cannot_reuse_an_identity():
    with pytest.raises(IdentityError):
        assert_version_immutable(
            StrategyIdentity("golden_cross", "1.0.0"),
            StrategyIdentity("range_breakout", "1.0.0"),
            definition_changed=True,
        )


def test_chain_identity_is_its_own_type():
    chain = ChainIdentity("gold_context_trigger", "1.0.0")
    assert chain.canonical == "gold_context_trigger@1.0.0"
    assert not isinstance(chain, StrategyIdentity)
