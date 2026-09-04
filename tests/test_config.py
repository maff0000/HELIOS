"""External configuration: validated at startup, loud when incomplete."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from helios.config import (
    OPTIONAL_SETTINGS,
    REQUIRED_SETTINGS,
    HeliosConfig,
    load_config,
)
from helios.contracts import Timeframe
from helios.errors import ConfigurationError

COMPLETE_ENV = {
    "HELIOS_ENVIRONMENT": "test",
    "HELIOS_LOG_LEVEL": "INFO",
    "HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER": "2.0",
    "HELIOS_FRESHNESS_GRACE_SECONDS": "30",
    "HELIOS_FRESHNESS_ALLOW_INCOMPLETE_FRAMES": "false",
    "HELIOS_ACCEPTED_HERMES_SCHEMA_VERSIONS": "hermes.market_fact/1.0.0",
}


def test_an_empty_environment_fails_and_reports_every_problem_at_once():
    with pytest.raises(ConfigurationError) as caught:
        load_config({})
    problems = caught.value.context["problems"]
    assert len(problems) == len(REQUIRED_SETTINGS)
    for attribute, env_var, _path in REQUIRED_SETTINGS:
        assert any(item.startswith(f"{attribute}:") for item in problems)
        assert any(env_var in item for item in problems)


@pytest.mark.parametrize("missing", [name for name in COMPLETE_ENV])
def test_each_required_value_is_genuinely_required(missing):
    environ = {key: value for key, value in COMPLETE_ENV.items() if key != missing}
    with pytest.raises(ConfigurationError):
        load_config(environ)


def test_a_complete_environment_loads():
    config = load_config(COMPLETE_ENV)
    assert config.environment == "test"
    assert config.freshness_max_age_multiplier == "2.0"
    assert config.freshness_grace_seconds == 30
    assert config.accepted_hermes_schema_versions == ("hermes.market_fact/1.0.0",)


def test_the_example_file_is_a_working_configuration(repo_root):
    """The documented example must actually load, or it is not documentation."""
    config = load_config({}, config_file=repo_root / "config" / "helios.example.toml")
    assert config.environment
    assert config.freshness_policy().max_age_for(Timeframe.H4) > timedelta(0)


def test_the_environment_overrides_the_file(repo_root):
    """One image per environment; only the injected environment differs."""
    config = load_config(
        {"HELIOS_ENVIRONMENT": "prod", "HELIOS_LOG_LEVEL": "WARNING"},
        config_file=repo_root / "config" / "helios.example.toml",
    )
    assert config.environment == "prod"
    assert config.log_level == "WARNING"


def test_freshness_overrides_load_from_a_file(config):
    assert config.freshness_overrides[Timeframe.D1] == timedelta(seconds=172800)


def test_freshness_overrides_load_from_the_environment():
    config = load_config({**COMPLETE_ENV, "HELIOS_FRESHNESS_OVERRIDES": "H4=21600,M5=600"})
    assert config.freshness_overrides[Timeframe.H4] == timedelta(seconds=21600)
    assert config.freshness_overrides[Timeframe.M5] == timedelta(seconds=600)


@pytest.mark.parametrize(
    "overrides",
    [
        {"HELIOS_LOG_LEVEL": "CHATTY"},
        {"HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER": "0"},
        {"HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER": "abc"},
        {"HELIOS_FRESHNESS_GRACE_SECONDS": "-5"},
        {"HELIOS_FRESHNESS_GRACE_SECONDS": "soon"},
        {"HELIOS_FRESHNESS_ALLOW_INCOMPLETE_FRAMES": "maybe"},
        {"HELIOS_ACCEPTED_HERMES_SCHEMA_VERSIONS": ""},
        {"HELIOS_FRESHNESS_OVERRIDES": "H3=600"},
        {"HELIOS_FRESHNESS_OVERRIDES": "H4=notanumber"},
        {"HELIOS_FRESHNESS_OVERRIDES": "H4"},
        {"HELIOS_STRATEGY_PACKAGE_DIR": "/nonexistent/helios/packages"},
    ],
)
def test_invalid_values_fail_loudly(overrides):
    with pytest.raises(ConfigurationError):
        load_config({**COMPLETE_ENV, **overrides})


def test_a_named_but_absent_config_file_fails_loudly():
    with pytest.raises(ConfigurationError) as caught:
        load_config({"HELIOS_CONFIG_FILE": "/nonexistent/helios.toml"})
    assert "not found" in str(caught.value)


def test_a_malformed_config_file_fails_loudly(tmp_path):
    path = tmp_path / "broken.toml"
    path.write_text("this is = not [valid toml\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        load_config({}, config_file=path)
    assert "malformed configuration file" in str(caught.value)


def test_config_is_immutable(config):
    with pytest.raises(Exception):
        config.log_level = "DEBUG"  # type: ignore[misc]


def test_the_policy_is_built_from_configuration_alone(config):
    policy = config.freshness_policy()
    assert policy.max_age_multiplier == config.freshness_max_age_multiplier
    assert policy.grace == timedelta(seconds=config.freshness_grace_seconds)
    assert policy.allow_incomplete_frames is config.freshness_allow_incomplete_frames


def test_incompatible_upstream_schema_versions_are_rejected(config):
    assert config.accepts_schema_version("hermes.market_fact/1.0.0")
    assert not config.accepts_schema_version("hermes.market_fact/2.0.0")


def test_no_configuration_default_is_baked_into_source():
    """Every setting must come from outside; source supplies no fallback."""
    import dataclasses

    for field in dataclasses.fields(HeliosConfig):
        assert field.default is dataclasses.MISSING, field.name
        assert field.default_factory is dataclasses.MISSING, field.name
    known = {name for name, _var, _path in REQUIRED_SETTINGS + OPTIONAL_SETTINGS}
    assert {field.name for field in dataclasses.fields(HeliosConfig)} == known


def test_no_source_file_contains_a_credential_shaped_setting(repo_root):
    """HELIOS needs no credential; none may appear even as a placeholder.

    Identifiers and literals are checked, not prose: config.py's docstring
    saying "nothing here reads a secret" is the boundary being documented.
    """
    from tests._scan import code_tokens, python_files, strip_comment_lines

    banned = ("password", "secret", "api_key", "apikey", "access_token",
              "connection_string", "private_key", "passwd")
    for path in python_files(repo_root / "helios"):
        for kind, text in code_tokens(path):
            lowered = text.lower()
            for word in banned:
                assert word not in lowered, f"{path}: {kind} {text!r} mentions {word}"
    example = strip_comment_lines(
        (repo_root / "config" / "helios.example.toml").read_text(encoding="utf-8")
    ).lower()
    for word in banned:
        assert word not in example


def test_config_paths_are_paths(config):
    assert config.strategy_package_dir is None or isinstance(config.strategy_package_dir, Path)
