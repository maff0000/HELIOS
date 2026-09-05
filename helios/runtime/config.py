"""What the running service must be told, and what those settings mean.

There are no runtime defaults in this file. Every value below is supplied
externally — ``HELIOS_*`` environment variables or the TOML file named by
``HELIOS_CONFIG_FILE`` — and is read, shaped and range-checked by
:func:`helios.config.load_config`, which remains the single configuration entry
point. This module holds only the validated *shape* the service is handed and
the rule for which of those settings the service cannot start without.

The split is deliberate. ``load_config`` validates what every HELIOS process
needs; it does not demand runtime settings of a process that is not the
runtime, because a contract test or a one-off script has no feed and no status
file. :meth:`helios.config.HeliosConfig.runtime` is where "and this process
intends to *run*" is asserted, and it reports every missing setting at once, the
same way ``load_config`` reports every malformed one at once.

Settings
--------

=========================================  ==================================  ===========================================
environment variable                       TOML                                meaning
=========================================  ==================================  ===========================================
``HELIOS_RUNTIME_INSTRUMENT``              ``[runtime] instrument``            the subject this deployment evaluates
``HELIOS_RUNTIME_FEED_DIR``                ``[runtime] feed_dir``              directory of HERMES fact documents
``HELIOS_STRATEGY_PACKAGE_DIR``            ``[strategies] package_dir``        directory of HSA strategy packages
``HELIOS_RUNTIME_TICK_SECONDS``            ``[runtime] tick_seconds``          wall-clock pause between evaluations
``HELIOS_RUNTIME_ON_FEED_END``             ``[runtime] on_feed_end``           ``STOP`` or ``REPEAT``
``HELIOS_RUNTIME_STATUS_FILE``             ``[runtime] status_file``           where health/readiness is written
``HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS``  ``[runtime] health_max_age_seconds``  how stale that file may be
``HELIOS_RUNTIME_MAX_CYCLES``              ``[runtime] max_cycles``            optional bound on evaluations
=========================================  ==================================  ===========================================

Nothing here reads a secret. Evaluating strategies against market facts needs
no credential, and if a later work item ever needs one it belongs in the
environment and must never be logged, echoed or published.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: Stop cleanly when the ordered feed is exhausted.
ON_FEED_END_STOP = "STOP"
#: Replay the ordered feed from its first instant again. A v1 deployment has no
#: live upstream, so "keep running" can only mean "replay". Each pass is a fresh
#: replay rather than a continuation — the next instant precedes the one just
#: evaluated, and an occurrence cannot be carried backwards across that — so the
#: service rearms to a cold start at the boundary and logs that it did. Every
#: pass therefore publishes identical bytes, which is the determinism claim
#: running continuously rather than asserted once.
ON_FEED_END_REPEAT = "REPEAT"

#: Every action an operator may configure for feed exhaustion.
SUPPORTED_FEED_END_ACTIONS: tuple[str, ...] = (ON_FEED_END_REPEAT, ON_FEED_END_STOP)

#: The settings the service refuses to start without, as
#: (attribute, environment variable, TOML path). Used to report every missing
#: one at once rather than one restart at a time.
REQUIRED_FOR_RUNTIME: tuple[tuple[str, str, str], ...] = (
    ("instrument", "HELIOS_RUNTIME_INSTRUMENT", "[runtime] instrument"),
    ("feed_dir", "HELIOS_RUNTIME_FEED_DIR", "[runtime] feed_dir"),
    ("package_dir", "HELIOS_STRATEGY_PACKAGE_DIR", "[strategies] package_dir"),
    ("tick_seconds", "HELIOS_RUNTIME_TICK_SECONDS", "[runtime] tick_seconds"),
    ("on_feed_end", "HELIOS_RUNTIME_ON_FEED_END", "[runtime] on_feed_end"),
    ("status_file", "HELIOS_RUNTIME_STATUS_FILE", "[runtime] status_file"),
    (
        "health_max_age_seconds",
        "HELIOS_RUNTIME_HEALTH_MAX_AGE_SECONDS",
        "[runtime] health_max_age_seconds",
    ),
)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Validated runtime configuration. Immutable once loaded."""

    instrument: str
    feed_dir: Path
    package_dir: Path
    tick_seconds: int
    on_feed_end: str
    status_file: Path
    health_max_age_seconds: int
    max_cycles: Optional[int] = None

    @property
    def repeats_feed(self) -> bool:
        return self.on_feed_end == ON_FEED_END_REPEAT

    def describe(self) -> dict[str, object]:
        """A log-safe description of how this deployment was configured."""
        return {
            "runtime_instrument": self.instrument,
            "runtime_feed_dir": str(self.feed_dir),
            "runtime_package_dir": str(self.package_dir),
            "runtime_tick_seconds": self.tick_seconds,
            "runtime_on_feed_end": self.on_feed_end,
            "runtime_status_file": str(self.status_file),
            "runtime_health_max_age_seconds": self.health_max_age_seconds,
            "runtime_max_cycles": self.max_cycles,
        }
