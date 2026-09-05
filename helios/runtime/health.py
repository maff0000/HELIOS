"""Health and readiness, without opening a port.

The PID asks for two things that look as though they pull against each other:
health/readiness must work, and there must be **no public internal state
service by default**. They only pull against each other if you assume health
has to be an HTTP endpoint. It does not.

HELIOS reports health the way it reports everything else — by writing a file
and by writing structured logs. The running service maintains one small status
document; a probe reads it, judges it against configuration, and exits ``0`` or
``1``. That is a complete health and readiness mechanism with **no listening
socket, no bound port and no wire protocol**: nothing in ``helios/`` may even
import a networking library, and ``tests/test_execution_blind.py`` enforces it.

    python3 -m helios.runtime.health            # readiness and liveness
    python3 -m helios.runtime.health --check ready
    python3 -m helios.runtime.health --check live

The probe reads the same configuration the service did, so a container's
``HEALTHCHECK`` needs no arguments, no duplicated paths and no second source of
truth. The judgement is stated, not implied:

**Ready** — the service loaded its definitions, opened its feed and completed a
full evaluation. Anything less is a process that is up but has published
nothing, which is not readiness.

**Live** — the status document was updated within the configured maximum age
and the service has not stopped. A wedged evaluation loop stops updating the
document and therefore stops being live; a service that has finished its feed
and exited cleanly is honestly reported as stopped rather than as unhealthy.

Two failure modes are deliberately distinguished from "unhealthy": a status
document that is **absent** (the service has not started yet, or was pointed
somewhere else) and one that is **unreadable** (something is writing over it).
Both exit non-zero, and both say which they were.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from helios.clock import from_iso8601_utc, to_iso8601_utc, utc_now
from helios.errors import HeliosError

#: The status document format a probe understands. Versioned for the same
#: reason the published envelope is: a reader that does not recognise it must
#: refuse rather than interpret it.
STATUS_SCHEMA_VERSION = "helios.runtime_status/1.0.0"

#: Loaded, but nothing evaluated yet.
PHASE_STARTING = "STARTING"
#: Evaluating and publishing.
PHASE_READY = "READY"
#: A stop was requested; finishing the current evaluation and flushing.
PHASE_STOPPING = "STOPPING"
#: Finished. The sink is flushed and closed.
PHASE_STOPPED = "STOPPED"

PHASES: tuple[str, ...] = (PHASE_READY, PHASE_STARTING, PHASE_STOPPED, PHASE_STOPPING)

#: What a probe may be asked to judge.
CHECK_READY = "ready"
CHECK_LIVE = "live"
CHECK_BOTH = "both"
CHECKS: tuple[str, ...] = (CHECK_BOTH, CHECK_LIVE, CHECK_READY)


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """What the service publishes about itself. Operational only.

    Deliberately NOT strategy state: counts and instants, never a verdict, a
    direction or a strength. Strategy state has exactly one destination — the
    configured publication sink — and a second, differently-shaped copy of it
    here is how two sources of truth are born.
    """

    phase: str
    environment: str
    instrument: str
    started_at_utc: datetime
    updated_at_utc: datetime
    cycles_completed: int
    envelopes_published: int
    strategies_bound: int
    chains_bound: int
    feed_passes_completed: int
    publication_sink: str
    schema_version: str = STATUS_SCHEMA_VERSION
    last_evaluated_at_utc: Optional[datetime] = None
    contained_failures: int = 0
    resolution: Optional[str] = None

    def to_document(self) -> dict[str, Any]:
        """The exact mapping written to the status file."""
        document = asdict(self)
        for key in ("started_at_utc", "updated_at_utc", "last_evaluated_at_utc"):
            value = document[key]
            document[key] = to_iso8601_utc(value) if value is not None else None
        return document

    def render(self) -> str:
        """Deterministic JSON — sorted keys, no insignificant whitespace."""
        return json.dumps(
            self.to_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def write_status(path: Path | str, status: RuntimeStatus) -> Path:
    """Write the status document atomically.

    Written to a sibling temporary file and renamed into place, so a probe
    reading concurrently sees either the previous document or the new one and
    never a half-written one. ``os.replace`` is atomic within a filesystem,
    which is why the temporary file is a sibling rather than somewhere tidier.
    """
    destination = Path(path)
    staging = destination.with_name(destination.name + ".partial")
    staging.write_text(status.render() + "\n", encoding="utf-8")
    os.replace(staging, destination)
    return destination


def read_status(path: Path | str) -> RuntimeStatus:
    """Read a status document, failing loudly on anything unrecognised."""
    source = Path(path)
    if not source.is_file():
        raise HeliosError("runtime status document not found", status_file=str(source))
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HeliosError(
            "runtime status document is malformed", status_file=str(source), detail=str(exc)
        ) from exc
    if not isinstance(document, Mapping):
        raise HeliosError(
            "runtime status document is not a mapping", status_file=str(source)
        )
    declared = document.get("schema_version")
    if declared != STATUS_SCHEMA_VERSION:
        raise HeliosError(
            "runtime status document declares a format this build does not read",
            status_file=str(source),
            received=declared,
            supported=STATUS_SCHEMA_VERSION,
        )
    fields = dict(document)
    for key in ("started_at_utc", "updated_at_utc", "last_evaluated_at_utc"):
        value = fields.get(key)
        fields[key] = from_iso8601_utc(str(value), field=key) if value is not None else None
    try:
        return RuntimeStatus(**fields)
    except TypeError as exc:
        raise HeliosError(
            "runtime status document does not match the format it declares",
            status_file=str(source),
            detail=str(exc),
        ) from exc


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One health judgement, and the reasoning behind it."""

    ready: bool
    live: bool
    checked: str
    detail: str
    status: Optional[RuntimeStatus] = None
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def satisfied(self) -> bool:
        if self.checked == CHECK_READY:
            return self.ready
        if self.checked == CHECK_LIVE:
            return self.live
        return self.ready and self.live

    def render(self) -> str:
        document: dict[str, Any] = {
            "checked": self.checked,
            "detail": self.detail,
            "live": self.live,
            "ready": self.ready,
            "satisfied": self.satisfied,
        }
        if self.problems:
            document["problems"] = list(self.problems)
        if self.status is not None:
            document["phase"] = self.status.phase
            document["cycles_completed"] = self.status.cycles_completed
            document["envelopes_published"] = self.status.envelopes_published
            document["updated_at_utc"] = to_iso8601_utc(self.status.updated_at_utc)
        return json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def probe(
    path: Path | str,
    *,
    max_age_seconds: int,
    now_utc: Optional[datetime] = None,
    check: str = CHECK_BOTH,
) -> ProbeResult:
    """Judge a status document. Reports; never raises for an unhealthy service."""
    if check not in CHECKS:
        raise HeliosError("unknown health check", check=check, supported=list(CHECKS))
    moment = utc_now() if now_utc is None else now_utc
    try:
        status = read_status(path)
    except HeliosError as error:
        return ProbeResult(
            ready=False,
            live=False,
            checked=check,
            detail=error.message,
            problems=(f"{type(error).__name__}: {error.message}",),
        )

    problems: list[str] = []
    age = moment - status.updated_at_utc
    if age > timedelta(seconds=max_age_seconds):
        problems.append(
            f"the status document was last updated {int(age.total_seconds())}s ago, "
            f"beyond the configured maximum of {max_age_seconds}s"
        )
    if status.phase == PHASE_STOPPED:
        problems.append("the service has stopped")
    if status.phase == PHASE_STARTING:
        problems.append("the service has not completed an evaluation yet")

    live = age <= timedelta(seconds=max_age_seconds) and status.phase in (
        PHASE_READY,
        PHASE_STARTING,
        PHASE_STOPPING,
    )
    ready = live and status.phase == PHASE_READY and status.cycles_completed > 0
    detail = (
        f"phase {status.phase}, {status.cycles_completed} evaluations completed, "
        f"{status.envelopes_published} envelopes published, status document "
        f"{int(age.total_seconds())}s old"
    )
    return ProbeResult(
        ready=ready,
        live=live,
        checked=check,
        detail=detail,
        status=status,
        problems=tuple(problems),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Probe this deployment's own service. Exits ``0`` when satisfied.

    Reads the same configuration the service reads, so there is one source of
    truth for where the status document lives and how stale it may be.
    """
    from helios.config import load_config

    parser = argparse.ArgumentParser(
        prog="python3 -m helios.runtime.health",
        description="Report whether this HELIOS deployment is ready and live.",
    )
    parser.add_argument("--check", choices=list(CHECKS), default=CHECK_BOTH)
    arguments = parser.parse_args(argv)

    try:
        runtime = load_config().runtime()
    except HeliosError as error:
        sys.stdout.write(
            json.dumps(
                {
                    "checked": arguments.check,
                    "detail": error.message,
                    "live": False,
                    "ready": False,
                    "satisfied": False,
                    "problems": [f"{type(error).__name__}: {error.message}"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 1

    result = probe(
        runtime.status_file,
        max_age_seconds=runtime.health_max_age_seconds,
        check=arguments.check,
    )
    sys.stdout.write(result.render() + "\n")
    return 0 if result.satisfied else 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
