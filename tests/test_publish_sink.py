"""Publication sinks, the publisher, and the external configuration behind them."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path

import pytest

from helios.errors import ConfigurationError, ContractViolationError, HeliosError
from helios.integration.exemplars import golden_exemplars
from helios.observability.logging import JsonFormatter, configure_logging
from helios.publish import (
    RECORD_TERMINATOR,
    SINK_FILE,
    SINK_MEMORY,
    SINK_STREAM,
    SUPPORTED_SINKS,
    SUPPORTED_STREAMS,
    FileSink,
    InMemorySink,
    PublicationConfig,
    PublicationSink,
    StatePublisher,
    StreamSink,
    build_sink,
)
from helios.config import load_config
from tests.conftest import COMPLETE_ENV

#: Everything a HELIOS deployment needs EXCEPT its publication settings.
#: ``load_config`` is the single configuration entry point, so a test about the
#: publication layer still has to supply the rest of a valid deployment; the
#: publication settings themselves are left entirely to each test.
BASE_ENV = {
    key: value
    for key, value in COMPLETE_ENV.items()
    if not key.startswith("HELIOS_PUBLICATION_")
}


def publication(environ=None, *, config_file=None):
    """The configured publication destination, via the one entry point."""
    return load_config(
        {**BASE_ENV, **(environ or {})}, config_file=config_file
    ).publication()


@pytest.fixture(scope="module")
def envelopes():
    return tuple(item.envelope for item in golden_exemplars())


@pytest.fixture
def envelope(envelopes):
    return envelopes[0]


# ------------------------------------------------------------------- the sinks


def test_every_sink_satisfies_the_sink_interface(tmp_path):
    for sink in (InMemorySink(), StreamSink(io.StringIO(), label="test"),
                 FileSink(tmp_path / "out.jsonl")):
        assert isinstance(sink, PublicationSink)
        sink.close()


def test_the_memory_sink_keeps_publication_order(envelopes):
    sink = InMemorySink()
    publisher = StatePublisher(sink)
    payloads = publisher.publish_all(envelopes)
    assert sink.records == payloads
    assert len(sink.records) == len(envelopes)


def test_the_stream_sink_writes_one_json_line_per_state(envelopes):
    stream = io.StringIO()
    sink = StreamSink(stream, label="test")
    publisher = StatePublisher(sink)
    payloads = publisher.publish_all(envelopes)
    publisher.close()
    text = stream.getvalue()
    assert text.endswith(RECORD_TERMINATOR)
    lines = text.splitlines()
    assert lines == list(payloads)
    for line in lines:
        json.loads(line)  # every line stands alone as a complete document


def test_the_file_sink_appends_and_is_readable_line_by_line(tmp_path, envelopes):
    path = tmp_path / "state" / "helios.jsonl"
    path.parent.mkdir()
    with StatePublisher(FileSink(path)) as publisher:
        first = publisher.publish_all(envelopes[:2])
    with StatePublisher(FileSink(path)) as publisher:
        second = publisher.publish_all(envelopes[2:])
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == list(first) + list(second)


def test_the_file_sink_flushes_each_record_as_it_is_published(tmp_path, envelope):
    """A consumer tailing the file must see state when it is published."""
    path = tmp_path / "helios.jsonl"
    sink = FileSink(path)
    try:
        StatePublisher(sink).publish(envelope)
        assert path.read_text(encoding="utf-8").strip() == envelope.to_canonical_json()
    finally:
        sink.close()


def test_a_file_sink_refuses_a_directory_that_does_not_exist(tmp_path):
    """A mistyped destination fails at startup, not quietly into nowhere."""
    with pytest.raises(ConfigurationError) as caught:
        FileSink(tmp_path / "absent" / "helios.jsonl")
    assert "does not exist" in str(caught.value)


def test_a_file_sink_refuses_a_path_that_is_not_a_file(tmp_path):
    with pytest.raises(ConfigurationError):
        FileSink(tmp_path)


def test_a_closed_sink_refuses_further_publication(tmp_path, envelope):
    for sink in (InMemorySink(), StreamSink(io.StringIO(), label="test"),
                 FileSink(tmp_path / "out.jsonl")):
        sink.close()
        with pytest.raises(HeliosError) as caught:
            sink.emit("{}")
        assert "closed" in str(caught.value)


def test_closing_twice_is_harmless(tmp_path):
    sink = FileSink(tmp_path / "out.jsonl")
    sink.close()
    sink.close()
    assert sink.is_closed


def test_a_stream_sink_does_not_close_a_stream_it_does_not_own():
    stream = io.StringIO()
    StreamSink(stream, label="test").close()
    assert not stream.closed
    owned = io.StringIO()
    StreamSink(owned, label="test", owns_stream=True).close()
    assert owned.closed


def test_a_stream_sink_refuses_something_that_cannot_be_written_to():
    with pytest.raises(ConfigurationError):
        StreamSink(object(), label="test")  # type: ignore[arg-type]


# --------------------------------------------------------------- the publisher


def test_the_publisher_emits_the_canonical_bytes(envelope):
    sink = InMemorySink()
    payload = StatePublisher(sink).publish(envelope)
    assert payload == envelope.to_canonical_json()


def test_render_and_publish_produce_identical_bytes(envelope):
    publisher = StatePublisher(InMemorySink())
    assert publisher.render(envelope) == publisher.publish(envelope)


def test_publication_adds_nothing_to_the_payload(envelope):
    """No publication timestamp, no sequence number, no host identity."""
    published = json.loads(StatePublisher(InMemorySink()).publish(envelope))
    assert set(published) == set(json.loads(envelope.to_canonical_json()))


def test_two_publishers_publish_identical_bytes_for_identical_state(envelope):
    """Determinism across processes is what makes the golden files meaningful."""
    first = StatePublisher(InMemorySink()).publish(envelope)
    second = StatePublisher(InMemorySink()).publish(envelope)
    assert first == second


def test_the_publisher_counts_what_it_published(envelopes):
    publisher = StatePublisher(InMemorySink())
    assert publisher.published_count == 0
    publisher.publish_all(envelopes)
    assert publisher.published_count == len(envelopes)


def test_only_a_normalised_envelope_may_be_published():
    publisher = StatePublisher(InMemorySink())
    for value in ({"strategy_id": "golden_cross"}, "a string", None, 42):
        with pytest.raises(ContractViolationError) as caught:
            publisher.publish(value)  # type: ignore[arg-type]
        assert "StrategyStateEnvelope" in str(caught.value)


def test_a_publisher_requires_a_real_sink():
    with pytest.raises(ContractViolationError):
        StatePublisher(object())  # type: ignore[arg-type]


def test_the_publisher_logs_structured_utc_json(envelope):
    stream = io.StringIO()
    logger = configure_logging("DEBUG", stream=stream)
    try:
        StatePublisher(InMemorySink(), logger=logger).publish(envelope)
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
    line = stream.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["message"] == "published strategy state"
    assert record["strategy_id"] == str(envelope.strategy_id)
    assert record["strategy_state"] == envelope.state.value
    assert record["ts_utc"].endswith("Z")
    assert record["last_evaluated_at_utc"].endswith("Z")
    assert isinstance(JsonFormatter(), JsonFormatter)


def test_the_publisher_closes_its_sink(envelope):
    sink = InMemorySink()
    with StatePublisher(sink) as publisher:
        publisher.publish(envelope)
    assert sink.is_closed


# ------------------------------------------------------------- configuration


def test_the_sink_is_never_chosen_in_source():
    """No sink is defaulted anywhere: omitting it is a fatal, named problem."""
    with pytest.raises(ConfigurationError) as caught:
        publication({})
    assert "publication_sink: required configuration is missing" in str(caught.value)


def test_the_environment_configures_a_file_sink(tmp_path):
    config = publication(
        {
            "HELIOS_PUBLICATION_SINK": "file",
            "HELIOS_PUBLICATION_PATH": str(tmp_path / "helios.jsonl"),
        }
    )
    assert config.sink == SINK_FILE
    assert config.path == tmp_path / "helios.jsonl"
    sink = build_sink(config)
    try:
        assert isinstance(sink, FileSink)
    finally:
        sink.close()


def test_the_environment_configures_a_stream_sink():
    config = publication(
        {"HELIOS_PUBLICATION_SINK": "STREAM", "HELIOS_PUBLICATION_STREAM": "stdout"}
    )
    assert (config.sink, config.stream) == (SINK_STREAM, "STDOUT")
    sink = build_sink(config)
    assert isinstance(sink, StreamSink)
    assert config.description == "stream:stdout"


def test_a_config_file_supplies_the_same_settings(tmp_path):
    config_file = tmp_path / "helios.toml"
    config_file.write_text(
        "[publication]\n"
        "sink = \"FILE\"\n"
        f"path = \"{tmp_path / 'from-file.jsonl'}\"\n",
        encoding="utf-8",
    )
    config = publication({}, config_file=config_file)
    assert config.sink == SINK_FILE
    assert config.path == tmp_path / "from-file.jsonl"


def test_the_environment_wins_over_the_config_file(tmp_path):
    """One image everywhere; only the injected environment differs."""
    config_file = tmp_path / "helios.toml"
    config_file.write_text(
        "[publication]\nsink = \"FILE\"\npath = \"/nowhere/from-file.jsonl\"\n",
        encoding="utf-8",
    )
    config = publication(
        {"HELIOS_PUBLICATION_SINK": "MEMORY"}, config_file=config_file
    )
    assert config.sink == SINK_MEMORY
    assert config.path is None


def test_an_unknown_sink_is_refused_by_name():
    with pytest.raises(ConfigurationError) as caught:
        publication({"HELIOS_PUBLICATION_SINK": "HTTP"})
    message = str(caught.value)
    assert "HTTP" in message
    assert "FILE" in message


def test_a_file_sink_without_a_destination_is_refused():
    with pytest.raises(ConfigurationError) as caught:
        publication({"HELIOS_PUBLICATION_SINK": "FILE"})
    assert "publication_path: sink FILE requires a destination" in str(caught.value)


def test_a_stream_sink_without_a_destination_is_refused():
    with pytest.raises(ConfigurationError) as caught:
        publication({"HELIOS_PUBLICATION_SINK": "STREAM"})
    assert "publication_stream: sink STREAM requires a destination" in str(caught.value)


def test_settings_belonging_to_another_sink_are_refused():
    """Configuring something HELIOS is not doing is a contradiction, not extra."""
    with pytest.raises(ConfigurationError) as caught:
        publication(
            {"HELIOS_PUBLICATION_SINK": "MEMORY", "HELIOS_PUBLICATION_PATH": "/tmp/x"}
        )
    assert "publication_path: meaningful only for sink FILE" in str(caught.value)


def test_every_problem_is_reported_at_once():
    """An operator fixes one deployment, not one restart at a time."""
    with pytest.raises(ConfigurationError) as caught:
        publication(
            {"HELIOS_PUBLICATION_SINK": "FILE", "HELIOS_PUBLICATION_STREAM": "SYSLOG"}
        )
    problems = caught.value.context["problems"]
    assert len(problems) >= 2
    assert problems == sorted(problems)


def test_a_named_config_file_that_is_absent_is_fatal(tmp_path):
    with pytest.raises(ConfigurationError) as caught:
        publication({}, config_file=tmp_path / "absent.toml")
    assert "named but not found" in str(caught.value)


def test_a_malformed_config_file_is_fatal(tmp_path):
    config_file = tmp_path / "broken.toml"
    config_file.write_text("[publication\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        publication({}, config_file=config_file)
    assert "malformed configuration file" in str(caught.value)


def test_publication_is_loaded_by_the_single_configuration_loader():
    """One entry point, one precedence rule, one report of what is wrong.

    Publication settings used to be read by a loader of their own. A single
    load now reports a freshness problem and a publication problem together,
    which is the point: an operator fixes one deployment rather than one
    restart at a time.
    """
    with pytest.raises(ConfigurationError) as caught:
        load_config(
            {
                **BASE_ENV,
                "HELIOS_FRESHNESS_GRACE_SECONDS": "-5",
                "HELIOS_PUBLICATION_SINK": "HTTP",
            }
        )
    problems = caught.value.context["problems"]
    assert any(item.startswith("freshness_grace_seconds:") for item in problems)
    assert any(item.startswith("publication_sink:") for item in problems)
    assert problems == sorted(problems)


def test_the_config_file_is_read_once_for_every_setting(repo_root):
    """The documented example configures freshness AND publication together."""
    config = load_config({}, config_file=repo_root / "config" / "helios.example.toml")
    assert config.publication().sink == SINK_STREAM
    assert config.publication() == config.publication()
    assert build_sink(config.publication()).__class__ is StreamSink


def test_build_sink_requires_validated_configuration():
    with pytest.raises(ConfigurationError):
        build_sink({"sink": "MEMORY"})  # type: ignore[arg-type]


def test_a_publication_config_is_immutable():
    config = PublicationConfig(sink=SINK_MEMORY)
    with pytest.raises(Exception):
        config.sink = SINK_FILE  # type: ignore[misc]


# ------------------------------------------- no public internal state service


def test_the_only_destinations_are_a_file_a_stream_or_memory():
    """The PID forbids a public internal state service by default."""
    assert SUPPORTED_SINKS == (SINK_FILE, SINK_MEMORY, SINK_STREAM)
    assert SUPPORTED_STREAMS == ("STDERR", "STDOUT")


def test_the_publication_package_opens_no_network_and_binds_no_port():
    forbidden = {
        "socket", "socketserver", "ssl", "asyncio", "http", "https", "urllib",
        "requests", "httpx", "aiohttp", "flask", "fastapi", "uvicorn",
        "wsgiref", "xmlrpc", "ftplib", "smtplib", "telnetlib",
    }
    package = Path(__file__).resolve().parents[1] / "helios" / "publish"
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden, path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden, path


def test_an_overridden_setting_is_an_override_not_a_contradiction(tmp_path):
    """A FILE deployment file, run once with MEMORY, must not be a fatal error."""
    config_file = tmp_path / "helios.toml"
    config_file.write_text(
        "[publication]\nsink = \"FILE\"\npath = \"/var/lib/helios/state.jsonl\"\n",
        encoding="utf-8",
    )
    config = publication(
        {"HELIOS_PUBLICATION_SINK": "MEMORY"}, config_file=config_file
    )
    assert (config.sink, config.path) == (SINK_MEMORY, None)


def test_a_contradiction_written_in_one_place_is_still_refused(tmp_path):
    config_file = tmp_path / "helios.toml"
    config_file.write_text(
        "[publication]\nsink = \"MEMORY\"\npath = \"/var/lib/helios/state.jsonl\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as caught:
        publication({}, config_file=config_file)
    assert "publication_path: meaningful only for sink FILE" in str(caught.value)


def test_the_environment_contradicting_the_file_is_refused(tmp_path):
    """The environment is the more specific layer; it must not be ignored."""
    config_file = tmp_path / "helios.toml"
    config_file.write_text("[publication]\nsink = \"MEMORY\"\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as caught:
        publication(
            {"HELIOS_PUBLICATION_PATH": "/var/lib/helios/state.jsonl"},
            config_file=config_file,
        )
    assert "publication_path: meaningful only for sink FILE" in str(caught.value)
