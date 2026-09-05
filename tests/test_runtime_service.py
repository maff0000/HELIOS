"""The running service: startup, health, shutdown, restart, determinism.

What is proven here is the behaviour a container depends on and a strategy test
cannot show: that a wrong deployment is refused at startup rather than three
hours into a run, that health and readiness are answerable **without opening a
port**, that a stop is clean, and that restarting the service republishes byte
for byte what it published the first time.

The last one carries more weight than it looks. HELIOS keeps no store — the
only state between evaluations is each unit's own last published envelope, in
memory — so a restart genuinely re-derives everything from the ordered facts
and the definitions. If a restart produced different bytes, something other
than facts and definitions would be feeding the result.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from helios.clock import utc_now
from helios.config import load_config
from helios.errors import ConfigurationError, ContractViolationError
from helios.publish import InMemorySink, StatePublisher
from helios.runtime.config import ON_FEED_END_REPEAT
from helios.runtime.feed import load_feed
from helios.runtime.health import (
    CHECK_LIVE,
    CHECK_READY,
    PHASE_READY,
    PHASE_STARTING,
    PHASE_STOPPED,
    STATUS_SCHEMA_VERSION,
    probe,
    read_status,
)
from helios.runtime.service import (
    RESOLUTION_FEED_EXHAUSTED,
    RESOLUTION_MAX_CYCLES,
    RESOLUTION_STOP_REQUESTED,
    build_runtime,
)
from tests._scenario import (
    SCENARIO_FEED_DIR,
    build_scenario_runtime,
    scenario_environment,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class Recorded(logging.Handler):
    """Capture records from one logger, whatever the suite did to logging.

    ``configure_logging`` deliberately stops the ``helios`` tree propagating —
    a deployment's logs go to its configured handler and nowhere else — so
    pytest's ``caplog``, which listens on the root logger, sees nothing once
    any other test has configured logging. Attaching here observes the logger
    the service actually writes to, and is unaffected by test ordering.
    """

    def __init__(self, name: str, level: int = logging.DEBUG) -> None:
        super().__init__(level=level)
        self.records: list[logging.LogRecord] = []
        self._logger = logging.getLogger(name)
        self._previous = self._logger.level

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def __enter__(self) -> "Recorded":
        self._logger.addHandler(self)
        self._logger.setLevel(self.level)
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._logger.removeHandler(self)
        self._logger.setLevel(self._previous)

    def messages(self, message: str) -> list[logging.LogRecord]:
        return [record for record in self.records if record.getMessage() == message]


# --------------------------------------------------------- refusing to start


def test_an_incomplete_runtime_configuration_reports_every_problem_at_once():
    """One error naming everything wrong, not one restart per fault."""
    config = load_config({}, config_file=REPO_ROOT / "tests" / "data" / "helios.test.toml")
    with pytest.raises(ConfigurationError) as caught:
        config.runtime()
    problems = caught.value.context["problems"]
    named = " ".join(problems)
    for setting in (
        "HELIOS_RUNTIME_INSTRUMENT",
        "HELIOS_RUNTIME_FEED_DIR",
        "HELIOS_STRATEGY_PACKAGE_DIR",
        "HELIOS_RUNTIME_TICK_SECONDS",
        "HELIOS_RUNTIME_ON_FEED_END",
        "HELIOS_RUNTIME_STATUS_FILE",
        "HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS",
    ):
        assert setting in named, setting
    assert len(problems) == 7


def test_an_invalid_runtime_setting_is_refused_at_load(tmp_path):
    environment = scenario_environment(
        tmp_path / "status.json",
        HELIOS_RUNTIME_ON_FEED_END="SOMETIMES",
        HELIOS_RUNTIME_TICK_SECONDS="never",
    )
    with pytest.raises(ConfigurationError) as caught:
        load_config(environment)
    problems = " ".join(caught.value.context["problems"])
    assert "runtime_on_feed_end" in problems
    assert "runtime_tick_seconds" in problems


def test_a_status_file_in_a_directory_that_does_not_exist_is_refused(tmp_path):
    environment = scenario_environment(tmp_path / "nowhere" / "status.json")
    with pytest.raises(ConfigurationError) as caught:
        load_config(environment)
    assert any(
        "runtime_status_file" in problem for problem in caught.value.context["problems"]
    )


def test_a_feed_directory_that_does_not_exist_is_refused(tmp_path):
    environment = scenario_environment(
        tmp_path / "status.json", HELIOS_RUNTIME_FEED_DIR=str(tmp_path / "absent")
    )
    with pytest.raises(ConfigurationError) as caught:
        load_config(environment)
    assert any(
        "runtime_feed_dir" in problem for problem in caught.value.context["problems"]
    )


def test_a_feed_mixing_instruments_is_refused(tmp_path, config):
    """A deployment evaluates one subject; HELIOS will not guess which."""
    with pytest.raises(ContractViolationError) as caught:
        load_feed(
            SCENARIO_FEED_DIR,
            instrument="EUR_USD",
            accepted_schema_versions=config.accepted_hermes_schema_versions,
        )
    assert "mixes instruments" in caught.value.message


def test_two_documents_for_one_timeframe_are_refused(tmp_path, config):
    for name in ("xau_usd_aligned_m5.json", "xau_usd_aligned_h4.json"):
        (tmp_path / name).write_text(
            (SCENARIO_FEED_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "duplicate_m5.json").write_text(
        (SCENARIO_FEED_DIR / "xau_usd_aligned_m5.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(ContractViolationError) as caught:
        load_feed(
            tmp_path,
            instrument="XAU_USD",
            accepted_schema_versions=config.accepted_hermes_schema_versions,
        )
    assert "same timeframe" in caught.value.message


def test_documents_that_disagree_about_the_feed_cadence_are_refused(tmp_path, config):
    """One feed has one cadence, and HELIOS will not average them."""
    for name in ("xau_usd_aligned_m5.json", "xau_usd_aligned_h4.json"):
        document = json.loads((SCENARIO_FEED_DIR / name).read_text(encoding="utf-8"))
        if name.endswith("h4.json"):
            document["reference_now_utc"] = "2026-03-02T16:30:00Z"
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ContractViolationError) as caught:
        load_feed(
            tmp_path,
            instrument="XAU_USD",
            accepted_schema_versions=config.accepted_hermes_schema_versions,
        )
    assert "disagree about how long" in caught.value.message


def test_a_package_directory_with_no_atomic_strategy_is_refused(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "keep.txt").write_text("not a package", encoding="utf-8")
    environment = scenario_environment(
        tmp_path / "status.json", HELIOS_STRATEGY_PACKAGE_DIR=str(packages)
    )
    with pytest.raises(ConfigurationError):
        build_runtime(load_config(environment), publisher=StatePublisher(InMemorySink()))


# -------------------------------------------------------------- the loop ends


def test_the_run_ends_when_the_feed_is_exhausted(tmp_path):
    runtime = build_scenario_runtime(tmp_path / "status.json")
    report = runtime.run()
    assert report.resolution == RESOLUTION_FEED_EXHAUSTED
    assert report.cycles_completed == len(runtime.instants)
    assert report.envelopes_published == report.cycles_completed * 6
    assert report.feed_passes_completed == 1
    assert report.contained_failures == 0


def test_the_run_honours_a_configured_bound(tmp_path):
    runtime = build_scenario_runtime(
        tmp_path / "status.json", HELIOS_RUNTIME_MAX_CYCLES="5"
    )
    report = runtime.run()
    assert report.resolution == RESOLUTION_MAX_CYCLES
    assert report.cycles_completed == 5
    assert report.envelopes_published == 30


def test_a_stop_request_ends_the_run_cleanly(tmp_path):
    sink = InMemorySink()
    runtime = build_scenario_runtime(
        tmp_path / "status.json", publisher=StatePublisher(sink)
    )
    runtime.request_stop("a test asked it to")
    report = runtime.run()
    assert report.resolution == RESOLUTION_STOP_REQUESTED
    assert report.cycles_completed == 0
    assert sink.is_closed, "the sink must be flushed and released on shutdown"


def test_repeating_the_feed_republishes_identical_bytes(tmp_path):
    """A v1 deployment has no live upstream, so 'keep running' means 'replay'.

    A pass boundary steps evaluation time backwards, so it rearms to a cold
    start rather than carrying an occurrence across it — which is both the only
    coherent reading and what makes the second pass byte-identical.
    """
    sink = InMemorySink()
    runtime = build_scenario_runtime(
        tmp_path / "status.json",
        publisher=StatePublisher(sink),
        HELIOS_RUNTIME_ON_FEED_END=ON_FEED_END_REPEAT,
        HELIOS_RUNTIME_MAX_CYCLES=str(len(build_scenario_runtime(tmp_path / "probe.json").instants) * 2),
    )
    with Recorded("helios.runtime", logging.INFO) as recorded:
        report = runtime.run()
    assert report.resolution == RESOLUTION_MAX_CYCLES
    assert report.feed_passes_completed == 1
    half = len(sink.records) // 2
    assert sink.records[:half] == sink.records[half:]
    assert len(recorded.messages("replaying the feed from a cold start")) == 1


# ------------------------------------------------------------- health, no port


def test_readiness_is_answerable_without_a_listening_socket(tmp_path):
    """The PID asks for health AND for no public internal state service.

    Both, by writing a document and reading it. Nothing here binds a port, and
    ``tests/test_execution_blind.py`` proves no module under ``helios/`` may
    even import a networking library.
    """
    status_file = tmp_path / "status.json"
    runtime = build_scenario_runtime(status_file, HELIOS_RUNTIME_MAX_CYCLES="2")

    runtime._write_status(PHASE_STARTING)  # what run() writes before evaluating
    starting = probe(status_file, max_age_seconds=120)
    assert starting.live and not starting.ready
    assert "has not completed an evaluation" in " ".join(starting.problems)

    runtime.run()
    stopped = probe(status_file, max_age_seconds=120)
    assert not stopped.live and not stopped.ready
    assert read_status(status_file).phase == PHASE_STOPPED
    assert read_status(status_file).cycles_completed == 2


def test_a_service_mid_run_reports_ready(tmp_path):
    status_file = tmp_path / "status.json"
    runtime = build_scenario_runtime(status_file)
    runtime.evaluate_once(runtime.instants[0])
    result = probe(status_file, max_age_seconds=120)
    assert result.ready and result.live and result.satisfied
    assert read_status(status_file).phase == PHASE_READY
    assert "1 evaluations completed" in result.detail


def test_a_wedged_service_stops_being_live(tmp_path):
    status_file = tmp_path / "status.json"
    runtime = build_scenario_runtime(status_file)
    runtime.evaluate_once(runtime.instants[0])
    later = utc_now() + timedelta(seconds=600)
    result = probe(status_file, max_age_seconds=120, now_utc=later)
    assert not result.live and not result.ready
    assert "beyond the configured maximum" in " ".join(result.problems)


def test_an_absent_or_unreadable_status_document_is_distinguished(tmp_path):
    absent = probe(tmp_path / "nothing.json", max_age_seconds=120)
    assert not absent.satisfied
    assert "not found" in absent.detail

    unreadable = tmp_path / "broken.json"
    unreadable.write_text("{not json", encoding="utf-8")
    assert "malformed" in probe(unreadable, max_age_seconds=120).detail

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"schema_version": "someone.else/9.9.9"}), encoding="utf-8")
    assert "does not read" in probe(foreign, max_age_seconds=120).detail


def test_a_probe_can_be_asked_for_one_judgement(tmp_path):
    status_file = tmp_path / "status.json"
    runtime = build_scenario_runtime(status_file)
    runtime.evaluate_once(runtime.instants[0])
    assert probe(status_file, max_age_seconds=120, check=CHECK_READY).satisfied
    assert probe(status_file, max_age_seconds=120, check=CHECK_LIVE).satisfied


def test_the_status_document_is_operational_only(tmp_path):
    """Strategy state has exactly one destination: the publication sink."""
    status_file = tmp_path / "status.json"
    runtime = build_scenario_runtime(status_file)
    runtime.evaluate_once(runtime.instants[0])
    document = json.loads(status_file.read_text(encoding="utf-8"))
    assert document["schema_version"] == STATUS_SCHEMA_VERSION
    assert document["cycles_completed"] == 1
    assert document["envelopes_published"] == 6
    assert document["strategies_bound"] == 4
    assert document["chains_bound"] == 2
    for forbidden in ("state", "direction", "strength", "evidence", "components"):
        assert forbidden not in document, forbidden
    for key in ("started_at_utc", "updated_at_utc", "last_evaluated_at_utc"):
        assert document[key].endswith("Z"), key


def test_the_status_document_is_written_atomically(tmp_path):
    status_file = tmp_path / "status.json"
    runtime = build_scenario_runtime(status_file)
    runtime.evaluate_once(runtime.instants[0])
    assert not list(tmp_path.glob("*.partial")), "a staging file was left behind"


# ------------------------------------------------------------- observability


def test_the_freshness_decision_is_observable_before_it_is_acted_on(tmp_path):
    runtime = build_scenario_runtime(tmp_path / "status.json")
    with Recorded("helios.runtime", logging.DEBUG) as recorded:
        runtime.evaluate_once(runtime.instants[0])
    assessed = recorded.messages("market facts assessed")
    assert {record.timeframe for record in assessed} == {"H4", "H1", "M15", "M5"}
    for record in assessed:
        assert record.is_fresh is True
        assert record.age_seconds <= record.max_age_seconds
        assert record.evaluated_at_utc.tzinfo is not None


def test_every_evaluation_logs_what_it_published(tmp_path):
    runtime = build_scenario_runtime(tmp_path / "status.json")
    with Recorded("helios.runtime", logging.INFO) as recorded:
        runtime.evaluate_once(runtime.instants[0])
    completed = recorded.messages("evaluation complete")
    assert len(completed) == 1
    assert completed[0].envelopes_published == 6
    assert completed[0].atomic_strategies_evaluated == 4
    assert completed[0].chains_evaluated == 2


# ------------------------------------------------- restart, out of process


def run_service(tmp_path: Path, output: Path, **overrides: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment.pop("HELIOS_CONFIG_FILE", None)
    environment.update(
        scenario_environment(
            tmp_path / "status.json",
            HELIOS_PUBLICATION_SINK="FILE",
            HELIOS_PUBLICATION_PATH=str(output),
            **overrides,
        )
    )
    environment["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "helios.runtime"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )


def test_a_restart_republishes_byte_identical_state(tmp_path):
    """The restart proof and the deterministic-replay proof, in one run.

    Two separate processes, each starting from nothing, each writing to the
    same appended file. If the halves match, the second process derived its
    state from the ordered facts and the definitions alone.
    """
    output = tmp_path / "strategy_state.jsonl"
    first = run_service(tmp_path, output)
    assert first.returncode == 0, first.stderr
    after_first = output.read_text(encoding="utf-8").splitlines()
    assert after_first

    second = run_service(tmp_path, output)
    assert second.returncode == 0, second.stderr
    both = output.read_text(encoding="utf-8").splitlines()

    assert both[: len(after_first)] == after_first
    assert both[len(after_first) :] == after_first


def test_the_service_logs_structured_utc_json(tmp_path):
    output = tmp_path / "strategy_state.jsonl"
    completed = run_service(tmp_path, output, HELIOS_RUNTIME_MAX_CYCLES="3")
    assert completed.returncode == 0, completed.stderr
    lines = [json.loads(line) for line in completed.stderr.splitlines() if line.strip()]
    assert lines, completed.stderr
    for line in lines:
        assert line["ts_utc"].endswith("Z")
        assert set(line) >= {"ts_utc", "level", "logger", "message"}
    messages = [line["message"] for line in lines]
    assert "runtime starting" in messages
    assert "evaluation complete" in messages
    assert "runtime stopped" in messages


def test_a_deployment_that_cannot_start_says_so_and_exits_two(tmp_path):
    environment = dict(os.environ)
    environment.pop("HELIOS_CONFIG_FILE", None)
    for name in list(environment):
        if name.startswith("HELIOS_"):
            environment.pop(name)
    environment["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "helios.runtime"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )
    assert completed.returncode == 2
    report = json.loads(completed.stderr.strip().splitlines()[-1])
    assert report["error_type"] == "ConfigurationError"
    assert report["ts_utc"].endswith("Z")


def test_sigterm_is_a_clean_shutdown(tmp_path):
    """Stop evaluating, flush the sink, say why, exit non-violently."""
    output = tmp_path / "strategy_state.jsonl"
    environment = dict(os.environ)
    environment.pop("HELIOS_CONFIG_FILE", None)
    environment.update(
        scenario_environment(
            tmp_path / "status.json",
            HELIOS_PUBLICATION_SINK="FILE",
            HELIOS_PUBLICATION_PATH=str(output),
            HELIOS_RUNTIME_TICK_SECONDS="1",
        )
    )
    environment["PYTHONPATH"] = str(REPO_ROOT)
    process = subprocess.Popen(
        [sys.executable, "-m", "helios.runtime"],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if output.is_file() and output.read_text(encoding="utf-8").count("\n") >= 12:
                break
            time.sleep(0.2)
        else:  # pragma: no cover - only on a badly overloaded host
            process.kill()
            pytest.fail("the service published nothing within 60 seconds")
        process.send_signal(signal.SIGTERM)
        _, errors = process.communicate(timeout=60)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive cleanup
            process.kill()
            process.wait(timeout=30)

    assert process.returncode == 0, errors
    lines = [json.loads(line) for line in errors.splitlines() if line.strip()]
    stopping = [line for line in lines if line["message"] == "stop requested"]
    assert stopping and "SIGTERM" in stopping[0]["stop_reason"]
    stopped = [line for line in lines if line["message"] == "runtime stopped"]
    assert stopped and stopped[0]["resolution"] == RESOLUTION_STOP_REQUESTED
    assert stopped[0]["sink_flushed_and_closed"] is True

    status = read_status(tmp_path / "status.json")
    assert status.phase == PHASE_STOPPED
    assert status.resolution == RESOLUTION_STOP_REQUESTED

    published = output.read_text(encoding="utf-8").splitlines()
    assert published and len(published) % 6 == 0, "a payload was cut in half"
    assert all(line.endswith("}") for line in published)


def test_the_health_probe_runs_as_its_own_program(tmp_path):
    """What a container HEALTHCHECK actually invokes."""
    output = tmp_path / "strategy_state.jsonl"
    completed = run_service(tmp_path, output, HELIOS_RUNTIME_MAX_CYCLES="2")
    assert completed.returncode == 0, completed.stderr

    environment = dict(os.environ)
    environment.pop("HELIOS_CONFIG_FILE", None)
    environment.update(
        scenario_environment(
            tmp_path / "status.json",
            HELIOS_PUBLICATION_SINK="FILE",
            HELIOS_PUBLICATION_PATH=str(output),
        )
    )
    environment["PYTHONPATH"] = str(REPO_ROOT)
    probe_run = subprocess.run(
        [sys.executable, "-m", "helios.runtime.health"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=environment,
        timeout=60,
    )
    # The service has finished, so it is honestly reported as stopped.
    assert probe_run.returncode == 1
    report = json.loads(probe_run.stdout.strip())
    assert report["satisfied"] is False
    assert report["phase"] == PHASE_STOPPED
    assert report["cycles_completed"] == 2
