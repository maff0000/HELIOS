"""The Docker runtime, proven by building and running it.

The PID's runtime proof is not a claim about a Dockerfile; it is a claim about
an image that builds, starts, evaluates, reports health and restarts. So this
module builds the real image from the repository, runs it, and asserts what it
actually did.

**This module can never pass vacuously.** It is in two halves:

* the tests that read ``Dockerfile`` itself — a pinned base, a non-root user, a
  health check, no bound port, no secret — always run, on any host;
* the tests that build and run the image skip when Docker is unavailable, and
  say so **explicitly**: the skip reason names the exact command that failed
  and states in plain words that the runtime proof was not performed. With
  ``-ra`` in ``addopts`` (see ``pyproject.toml``) every skip reason is printed
  in the run summary, so a skipped runtime proof is visible in the output
  rather than hidden inside a dot.

Everything this module creates is named with a ``helios-`` prefix and a
run-unique suffix, and is removed again whether the test passed or failed.
Nothing it does touches an image, container or volume it did not create, and it
binds no host port.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"

#: How the container is configured for the proof. Every value is injected at
#: run time; none of it is in the image.
CONTAINER_ENVIRONMENT = {
    "HELIOS_ENVIRONMENT": "test",
    "HELIOS_LOG_LEVEL": "DEBUG",
    "HELIOS_FRESHNESS_MAX_AGE_MULTIPLIER": "1.5",
    "HELIOS_FRESHNESS_GRACE_SECONDS": "60",
    "HELIOS_FRESHNESS_ALLOW_INCOMPLETE_FRAMES": "false",
    "HELIOS_ACCEPTED_HERMES_SCHEMA_VERSIONS": "hermes.market_fact/1.0.0",
    "HELIOS_PUBLICATION_SINK": "FILE",
    "HELIOS_PUBLICATION_PATH": "/var/lib/helios/strategy_state.jsonl",
    "HELIOS_RUNTIME_INSTRUMENT": "XAU_USD",
    "HELIOS_RUNTIME_FEED_DIR": "/opt/helios/facts",
    "HELIOS_STRATEGY_PACKAGE_DIR": "/opt/helios/strategies",
    "HELIOS_RUNTIME_TICK_SECONDS": "0",
    "HELIOS_RUNTIME_ON_FEED_END": "STOP",
    "HELIOS_RUNTIME_STATUS_FILE": "/var/lib/helios/status.json",
    "HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS": "300",
}


def environment_arguments() -> list[str]:
    return [
        argument
        for name, value in sorted(CONTAINER_ENVIRONMENT.items())
        for argument in ("-e", f"{name}={value}")
    ]


# ------------------------------------------------- always-run: the definition


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.is_file(), "the repository must ship a Dockerfile"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_the_base_image_is_pinned_by_digest(dockerfile):
    """A tag moves; a digest does not. Determinism is about a build."""
    bases = re.findall(r"^FROM\s+(\S+)", dockerfile, flags=re.MULTILINE)
    assert bases, "the Dockerfile declares no base image"
    for base in bases:
        assert "@sha256:" in base, f"base image is not pinned by digest: {base}"
    assert len(set(bases)) == 1, "the build and runtime stages must share a base"


def test_the_image_runs_as_a_non_root_user(dockerfile):
    users = re.findall(r"^USER\s+(\S+)", dockerfile, flags=re.MULTILINE)
    assert users, "the Dockerfile never leaves root"
    assert users[-1].split(":")[0] not in ("root", "0")


def test_the_image_declares_a_health_check(dockerfile):
    assert "HEALTHCHECK" in dockerfile
    assert "helios.runtime.health" in dockerfile


def test_the_image_binds_no_port(dockerfile):
    """The PID forbids a public internal state service by default."""
    assert not re.search(r"^EXPOSE\b", dockerfile, flags=re.MULTILINE)


def test_nothing_secret_shaped_is_baked_into_the_image(dockerfile):
    """Instructions are checked, not prose.

    The same rule the rest of the suite applies: a comment must be able to NAME
    a boundary in order to explain it, and the Dockerfile's header says there
    is no credential in it. What must not appear is an INSTRUCTION carrying
    one.
    """
    from tests._scan import strip_comment_lines

    lowered = strip_comment_lines(dockerfile).lower()
    for term in (
        "password", "secret", "api_key", "apikey", "credential",
        "private_key", "access_token", "passwd",
    ):
        assert term not in lowered, f"the Dockerfile mentions {term}"
    # No HELIOS_* operational setting may be baked in: the image is promoted
    # unchanged and only the injected environment differs.
    for line in re.findall(r"^ENV\s+(.*)$", dockerfile, flags=re.MULTILINE):
        assert "HELIOS_" not in line, f"configuration baked into the image: {line}"


def test_the_build_context_excludes_what_must_not_ship():
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    for entry in (".git/", "tests/", ".env", "secrets/"):
        assert entry in ignore, entry


# ----------------------------------------------------- the build-and-run gate


def docker_unavailable() -> str | None:
    """Why the runtime proof cannot run here, or None if it can."""
    if os.environ.get("HELIOS_SKIP_DOCKER_TESTS"):
        return (
            "HELIOS_SKIP_DOCKER_TESTS is set: the Docker RUNTIME PROOF (build, "
            "start, evaluate, health, restart) was NOT performed on this host"
        )
    if shutil.which("docker") is None:
        return (
            "no `docker` executable on PATH: the Docker RUNTIME PROOF (build, "
            "start, evaluate, health, restart) was NOT performed on this host"
        )
    probe = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return (
            "`docker version` failed, so no daemon is reachable: the Docker "
            "RUNTIME PROOF (build, start, evaluate, health, restart) was NOT "
            "performed on this host. "
            f"stderr: {probe.stderr.strip()[:200]}"
        )
    return None


@pytest.fixture(scope="module")
def image() -> str:
    """Build the real image; remove it again however the tests end."""
    reason = docker_unavailable()
    if reason is not None:
        pytest.skip(reason)

    tag = f"helios-test-{uuid.uuid4().hex[:12]}:proof"
    built = subprocess.run(
        ["docker", "build", "-t", tag, "."],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if built.returncode != 0:
        pytest.fail(f"docker build failed:\n{built.stderr[-4000:]}")
    try:
        yield tag
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", tag], capture_output=True, text=True
        )


def container_name() -> str:
    return f"helios-test-{uuid.uuid4().hex[:12]}"


def remove(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


def test_the_image_is_built_from_the_repository_and_runs_as_a_non_root_user(image):
    completed = subprocess.run(
        ["docker", "run", "--rm", "--name", container_name(), image,
         "python3", "-c",
         "import os, json, helios; "
         "print(json.dumps({'uid': os.getuid(), 'module': helios.__file__}))"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["uid"] != 0
    assert "site-packages/helios" in report["module"], (
        "the image must run the INSTALLED distribution, not a mounted source tree"
    )


def test_a_container_evaluates_the_whole_slice_and_publishes_it(image, tmp_path):
    """Clean start, a real evaluation run, a clean exit."""
    name = container_name()
    try:
        completed = subprocess.run(
            ["docker", "run", "--name", name, *environment_arguments(), image],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert completed.returncode == 0, completed.stderr[-4000:]

        logs = [
            json.loads(line)
            for line in completed.stderr.splitlines()
            if line.strip().startswith("{")
        ]
        stopped = [line for line in logs if line["message"] == "runtime stopped"]
        assert stopped, completed.stderr[-2000:]
        assert stopped[0]["resolution"] == "FEED_EXHAUSTED"
        assert stopped[0]["cycles_completed"] == 61
        assert stopped[0]["envelopes_published"] == 366
        assert stopped[0]["contained_failures"] == 0
        assert stopped[0]["sink_flushed_and_closed"] is True

        # Every log line is one UTC JSON object with sorted keys.
        for line in logs:
            assert line["ts_utc"].endswith("Z")
            assert list(line) == sorted(line)

        # The freshness judgement is observable before it is acted on.
        assessed = [line for line in logs if line["message"] == "market facts assessed"]
        assert {line["timeframe"] for line in assessed} == {"H4", "H1", "M15", "M5"}
        assert all(line["is_fresh"] for line in assessed)

        published = subprocess.run(
            ["docker", "cp", f"{name}:/var/lib/helios/strategy_state.jsonl",
             str(tmp_path / "state.jsonl")],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert published.returncode == 0, published.stderr
    finally:
        remove(name)

    payloads = (tmp_path / "state.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(payloads) == 366

    from tests._scenario import replay

    assert tuple(payloads) == replay(tmp_path / "status.json"), (
        "the image must publish exactly what the source tree publishes"
    )


def test_a_container_reports_health_and_readiness_without_binding_a_port(image):
    """The probe a container HEALTHCHECK runs, in the container, mid-run."""
    name = container_name()
    try:
        started = subprocess.run(
            ["docker", "run", "-d", "--name", name, *environment_arguments(),
             "-e", "HELIOS_RUNTIME_TICK_SECONDS=1",
             "-e", "HELIOS_RUNTIME_ON_FEED_END=REPEAT", image],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert started.returncode == 0, started.stderr

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            probe = subprocess.run(
                ["docker", "exec", name, "python3", "-m", "helios.runtime.health"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:  # pragma: no cover - only on a badly overloaded host
            pytest.fail("the container never reported ready")

        report = json.loads(probe.stdout.strip())
        assert report["ready"] is True and report["live"] is True
        assert report["phase"] == "READY"
        assert report["cycles_completed"] > 0

        published = subprocess.run(
            ["docker", "port", name], capture_output=True, text=True, timeout=60
        )
        assert published.stdout.strip() == "", "the container bound a port"
    finally:
        remove(name)


def test_a_container_restarts_cleanly_and_republishes_identical_bytes(image):
    """Stop, start again, and compare. The restart proof and the replay proof.

    The container keeps no store, so the second run derives its state from the
    ordered facts and the definitions alone. If the halves differ, something
    other than facts and definitions is feeding the result.
    """
    name = container_name()
    try:
        first = subprocess.run(
            ["docker", "run", "--name", name, *environment_arguments(), image],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert first.returncode == 0, first.stderr[-4000:]

        second = subprocess.run(
            ["docker", "start", "-a", name],
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert second.returncode == 0, second.stderr[-4000:]

        compared = subprocess.run(
            ["docker", "run", "--rm", "--volumes-from", name, "--name",
             container_name(), image, "python3", "-c",
             "import json, pathlib; "
             "lines = pathlib.Path('/var/lib/helios/strategy_state.jsonl')"
             ".read_text().splitlines(); "
             "half = len(lines) // 2; "
             "print(json.dumps({'total': len(lines), "
             "'identical': lines[:half] == lines[half:]}))"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert compared.returncode == 0, compared.stderr
        report = json.loads(compared.stdout.strip().splitlines()[-1])
        assert report["total"] == 732
        assert report["identical"] is True
    finally:
        remove(name)


def test_a_container_given_no_configuration_refuses_to_start(image):
    completed = subprocess.run(
        ["docker", "run", "--rm", "--name", container_name(), image],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 2
    report = json.loads(completed.stderr.strip().splitlines()[-1])
    assert report["error_type"] == "ConfigurationError"
    assert report["level"] == "CRITICAL"
    assert report["ts_utc"].endswith("Z")
    assert len(report["error_context"]["problems"]) == 7
