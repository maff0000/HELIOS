"""The running HELIOS service.

One loop, stated plainly. On every evaluation instant the service:

1. asks the ordered feed which facts had arrived by then;
2. records how fresh each of those facts is, in the logs, before anything is
   evaluated against them;
3. evaluates every bound atomic strategy **concurrently**, each handed only its
   own declared window, its own declared parameters and its own last envelope;
4. composes every bound chain over those normalised envelopes;
5. publishes **every** resulting envelope — atomic and chain, matched and
   not — through the configured sink;
6. updates the status document a health probe reads.

Then it waits the configured interval and does it again, until the feed is
exhausted, a configured bound is reached, or a stop is requested.

What the service deliberately does not do
-----------------------------------------
It holds **no store**. The only state carried between evaluations is each
unit's own last published envelope, in memory, which is exactly what the
evaluation contract already defines as an input. Nothing is written anywhere
but the publication sink and the status document, because CER owns durable
evidence and HELIOS must not grow a competing one.

It adds **nothing** to a payload. No publication timestamp, no sequence number,
no host identity. That is what lets two runs of one feed be compared byte for
byte — which is the determinism claim, made checkable.

It knows **nothing** about downstream activity. There is no field, parameter or
channel here through which trade, position, broker or account information could
reach a strategy; the service's entire input is market facts and definitions.

Ordering is fixed, not incidental
---------------------------------
Atomic strategies are evaluated and published in canonical identity order and
chains after them, also in canonical identity order. Concurrency never reorders
results — :func:`helios.strategies.evaluate_concurrently` returns results in the
order supplied — so a concurrent run and a sequential one publish identical
bytes, and so do two runs of the same feed.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from types import FrameType
from typing import Any, Callable, Mapping, Optional, Sequence

from helios.clock import utc_now
from helios.composition.engine import ChainEngine
from helios.config import HeliosConfig, load_config
from helios.contracts._tokens import Instrument
from helios.contracts.freshness import FreshnessPolicy, assess_frame
from helios.contracts.output import StrategyStateEnvelope
from helios.contracts.timeframe import Timeframe
from helios.contracts.window import MarketFactWindow
from helios.errors import ConfigurationError, HeliosError
from helios.integration.hsa_boundary import StrategyBundle, load_handoff
from helios.observability.logging import configure_logging, get_logger
from helios.protocols import EvaluationContext
from helios.publish import StatePublisher, build_sink
from helios.runtime.config import RuntimeConfig
from helios.runtime.feed import MarketFactFeed, load_feed, minimum_frames_for
from helios.runtime.health import (
    PHASE_READY,
    PHASE_STARTING,
    PHASE_STOPPED,
    PHASE_STOPPING,
    RuntimeStatus,
    write_status,
)
from helios.spec.model import PackageKind
from helios.strategies.base import AtomicStrategy
from helios.strategies.catalogue import default_registry
from helios.strategies.evaluation import StrategyOutcome, evaluate_concurrently

#: The ordered feed ran out and the deployment asked to stop there.
RESOLUTION_FEED_EXHAUSTED = "FEED_EXHAUSTED"
#: The configured maximum number of evaluations was reached.
RESOLUTION_MAX_CYCLES = "MAX_CYCLES_REACHED"
#: A stop was requested — SIGTERM, SIGINT, or a caller.
RESOLUTION_STOP_REQUESTED = "STOP_REQUESTED"

#: Signals a clean shutdown is honoured for.
SHUTDOWN_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)


@dataclass(frozen=True, slots=True)
class CycleReport:
    """What one evaluation instant produced."""

    evaluated_at_utc: datetime
    envelopes: tuple[StrategyStateEnvelope, ...]
    payloads: tuple[str, ...]
    contained_failures: int = 0

    @property
    def published(self) -> int:
        return len(self.payloads)


@dataclass(frozen=True, slots=True)
class RunReport:
    """What a whole run produced, and why it ended."""

    cycles_completed: int
    envelopes_published: int
    feed_passes_completed: int
    contained_failures: int
    resolution: str
    last_evaluated_at_utc: Optional[datetime] = None


@dataclass(frozen=True, slots=True)
class BoundStrategies:
    """Everything this deployment was configured to evaluate."""

    atoms: tuple[AtomicStrategy, ...]
    chains: tuple[ChainEngine, ...]

    @property
    def minimum_history(self) -> Mapping[Timeframe, int]:
        """The deepest lookback each timeframe owes, across every atom."""
        return minimum_frames_for(
            [
                (declared.timeframe, declared.lookback)
                for atom in self.atoms
                for declared in atom.required_inputs()
            ]
        )

    def describe(self) -> dict[str, Any]:
        return {
            "atomic_strategies": [atom.identity.canonical for atom in self.atoms],
            "chains": [chain.identity.canonical for chain in self.chains],
        }


def bind_strategies(bundle: StrategyBundle) -> BoundStrategies:
    """Bind a resolved HSA handoff to the implementations this build ships.

    Order is canonical identity order at both levels, so what a deployment
    publishes never depends on how a directory happened to enumerate.
    """
    registry = default_registry()
    atoms = registry.build_all(
        package for package in bundle.packages.values() if package.kind is PackageKind.ATOMIC
    )
    chains = tuple(
        ChainEngine(bundle.packages[identity])
        for identity in bundle.identities
        if bundle.packages[identity].kind is PackageKind.CHAIN
    )
    if not atoms:
        raise ConfigurationError(
            "the configured strategy package directory holds no atomic strategy; "
            "HELIOS has nothing to evaluate",
            problems=["strategy_package_dir: no ATOMIC package found"],
        )
    return BoundStrategies(atoms=atoms, chains=chains)


class StrategyRuntime:
    """Evaluates and publishes continuously until asked to stop."""

    def __init__(
        self,
        *,
        config: HeliosConfig,
        runtime: RuntimeConfig,
        feed: MarketFactFeed,
        strategies: BoundStrategies,
        publisher: StatePublisher,
        logger: Optional[Any] = None,
        wall_clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._feed = feed
        self._strategies = strategies
        self._publisher = publisher
        self._logger = logger if logger is not None else get_logger("runtime")
        self._wall_clock = wall_clock
        self._policy: FreshnessPolicy = config.freshness_policy()
        self._instrument = Instrument(runtime.instrument)
        self._stop = threading.Event()
        self._previous_atomic: dict[str, StrategyStateEnvelope] = {}
        self._previous_chain: dict[str, StrategyStateEnvelope] = {}
        self._started_at_utc = wall_clock()
        self._cycles = 0
        self._published = 0
        self._passes = 0
        self._contained = 0
        self._last_evaluated_at_utc: Optional[datetime] = None
        self._instants = feed.instants(strategies.minimum_history)
        if not self._instants:
            raise ConfigurationError(
                "the configured feed offers no instant at which every declared "
                "input has the history its strategy package asked for",
                problems=[
                    "runtime_feed_dir: no evaluable instant",
                    f"required history: {sorted((tf.code, n) for tf, n in strategies.minimum_history.items())}",
                ],
            )

    # ------------------------------------------------------------- accessors

    @property
    def instants(self) -> tuple[datetime, ...]:
        """Every instant this run will evaluate at, in order."""
        return self._instants

    @property
    def publisher(self) -> StatePublisher:
        return self._publisher

    @property
    def feed(self) -> MarketFactFeed:
        return self._feed

    @property
    def strategies(self) -> BoundStrategies:
        return self._strategies

    def contexts_at(self, instant: datetime) -> Callable[[Any], EvaluationContext]:
        """The evaluation contexts this run would build for one instant.

        Public because it is the input half of the determinism claim: a test
        that wants to prove concurrent and sequential evaluation agree must be
        able to hand both the exact contexts the service would have handed
        them, rather than contexts it built for itself.
        """
        return self._context_builder(instant, self._feed.windows_at(instant))

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------- lifecycle

    def request_stop(self, reason: str = "a stop was requested") -> None:
        """Ask the loop to finish the current evaluation and shut down."""
        if not self._stop.is_set():
            self._logger.info("stop requested", extra={"stop_reason": reason})
        self._stop.set()

    def run(self) -> RunReport:
        """Evaluate continuously until the feed, a bound or a stop ends it."""
        self._logger.info(
            "runtime starting",
            extra={
                "helios_environment": self._config.environment,
                "publication_sink": self._publisher.sink.description,
                "evaluable_instants": len(self._instants),
                "first_instant_utc": self._instants[0],
                "final_instant_utc": self._instants[-1],
                **self._runtime.describe(),
                **self._feed.describe(),
                **self._strategies.describe(),
            },
        )
        self._write_status(PHASE_STARTING)
        resolution = RESOLUTION_FEED_EXHAUSTED
        try:
            resolution = self._loop()
        finally:
            self._write_status(PHASE_STOPPING, resolution=resolution)
            self._publisher.flush()
            self._publisher.close()
            self._write_status(PHASE_STOPPED, resolution=resolution)
        report = RunReport(
            cycles_completed=self._cycles,
            envelopes_published=self._published,
            feed_passes_completed=self._passes,
            contained_failures=self._contained,
            resolution=resolution,
            last_evaluated_at_utc=self._last_evaluated_at_utc,
        )
        self._logger.info(
            "runtime stopped",
            extra={
                "resolution": report.resolution,
                "cycles_completed": report.cycles_completed,
                "envelopes_published": report.envelopes_published,
                "feed_passes_completed": report.feed_passes_completed,
                "contained_failures": report.contained_failures,
                "sink_flushed_and_closed": True,
            },
        )
        return report

    def _loop(self) -> str:
        while True:
            for instant in self._instants:
                if self._stop.is_set():
                    return RESOLUTION_STOP_REQUESTED
                self.evaluate_once(instant)
                if (
                    self._runtime.max_cycles is not None
                    and self._cycles >= self._runtime.max_cycles
                ):
                    return RESOLUTION_MAX_CYCLES
                if self._pause():
                    return RESOLUTION_STOP_REQUESTED
            self._passes += 1
            self._logger.info(
                "feed pass complete",
                extra={
                    "feed_passes_completed": self._passes,
                    "runtime_on_feed_end": self._runtime.on_feed_end,
                },
            )
            if not self._runtime.repeats_feed:
                return RESOLUTION_FEED_EXHAUSTED
            self._rearm()

    def _rearm(self) -> None:
        """Forget every occurrence before replaying the feed from its start.

        A repeated pass is a *fresh replay*, not a continuation. The next
        instant precedes the one just evaluated, and an occurrence cannot be
        carried across a backwards step in evaluation time: the contract
        refuses an envelope whose match instant is later than the evaluation
        that published it, and it is right to — an occurrence that began after
        the evaluation reporting it is not a thing that can be true.

        So the pass boundary rearms to exactly the cold start a restart begins
        from, which is what makes every pass publish identical bytes and makes
        REPEAT mean what it says. It is logged, because a consumer tailing the
        sink sees the lifecycle begin again and must be able to tell that from
        a strategy resetting on its own.
        """
        self._previous_atomic.clear()
        self._previous_chain.clear()
        self._logger.info(
            "replaying the feed from a cold start",
            extra={
                "feed_passes_completed": self._passes,
                "occurrences_rearmed": True,
            },
        )

    def _pause(self) -> bool:
        """Wait the configured interval. Returns True if a stop arrived first.

        The wait is interruptible, so a SIGTERM during a long interval is
        honoured immediately rather than after it elapses.
        """
        if self._runtime.tick_seconds <= 0:
            return self._stop.is_set()
        return self._stop.wait(self._runtime.tick_seconds)

    # ------------------------------------------------------------ evaluation

    def evaluate_once(self, instant: datetime) -> CycleReport:
        """Evaluate and publish one instant. The whole slice, once."""
        windows = self._feed.windows_at(instant)
        self._record_freshness(instant, windows)

        outcomes = evaluate_concurrently(
            self._strategies.atoms, self._context_builder(instant, windows)
        )
        atomic_envelopes = tuple(outcome.envelope for outcome in outcomes)
        contained = self._report_containment(outcomes)

        chain_envelopes: list[StrategyStateEnvelope] = []
        for chain in self._strategies.chains:
            envelope = chain.evaluate(
                instrument=self._instrument,
                evaluated_at_utc=instant,
                components=atomic_envelopes,
                previous=self._previous_chain.get(chain.identity.canonical),
            )
            self._previous_chain[chain.identity.canonical] = envelope
            chain_envelopes.append(envelope)

        for outcome in outcomes:
            self._previous_atomic[outcome.identity.canonical] = outcome.envelope

        envelopes = atomic_envelopes + tuple(chain_envelopes)
        payloads = self._publisher.publish_all(envelopes)

        self._cycles += 1
        self._published += len(payloads)
        self._contained += contained
        self._last_evaluated_at_utc = instant
        self._logger.info(
            "evaluation complete",
            extra={
                "evaluated_at_utc": instant,
                "cycles_completed": self._cycles,
                "envelopes_published": len(payloads),
                "atomic_strategies_evaluated": len(atomic_envelopes),
                "chains_evaluated": len(chain_envelopes),
                "contained_failures": contained,
                "published_states": {
                    envelope.strategy_id: envelope.state.value for envelope in envelopes
                },
            },
        )
        self._write_status(PHASE_READY)
        return CycleReport(
            evaluated_at_utc=instant,
            envelopes=envelopes,
            payloads=payloads,
            contained_failures=contained,
        )

    def _context_builder(
        self, instant: datetime, windows: Mapping[Timeframe, MarketFactWindow]
    ) -> Callable[[Any], EvaluationContext]:
        """Build each strategy's context from its OWN declared bindings.

        A deployment may run two packages that bind one semantic role to
        different timeframes — the role-to-timeframe mapping belongs to a
        package and HELIOS holds no global opinion about it
        (``docs/CONTRACTS.md`` §1.1). So the window a strategy is handed for a
        role is chosen by *that strategy's* declaration, and a strategy is
        handed nothing it did not declare.
        """
        previous = dict(self._previous_atomic)
        policy = self._policy
        instrument = self._instrument

        def context_for(evaluator: Any) -> EvaluationContext:
            bound: dict[Any, MarketFactWindow] = {}
            for declared in evaluator.required_inputs():
                window = windows.get(declared.timeframe)
                if window is not None:
                    bound[declared.role] = window
            return EvaluationContext(
                instrument=instrument,
                evaluated_at_utc=instant,
                windows=bound,
                parameters=getattr(evaluator, "parameters", {}),
                freshness_policy=policy,
                previous=previous.get(evaluator.identity.canonical),
            )

        return context_for

    def _record_freshness(
        self, instant: datetime, windows: Mapping[Timeframe, MarketFactWindow]
    ) -> None:
        """Make the freshness judgement observable before it is acted on.

        The same verdict is published in every envelope's ``inputs``; logging it
        here means an operator watching a container can see *why* a strategy
        refused a fact, not only that it did.
        """
        for timeframe in sorted(windows):
            window = windows[timeframe]
            verdict = assess_frame(window.latest, self._policy, now_utc=instant)
            record = {
                "evaluated_at_utc": instant,
                "timeframe": timeframe.code,
                "frame_timestamp_utc": verdict.frame_timestamp_utc,
                "age_seconds": verdict.age_seconds,
                "max_age_seconds": verdict.max_age_seconds,
                "is_fresh": verdict.is_fresh,
                "is_complete": verdict.is_complete,
                "fact_source": verdict.source,
                "fact_schema_version": verdict.schema_version,
                "frames_available": len(window.frames),
            }
            if verdict.is_fresh and (
                verdict.is_complete or self._policy.allow_incomplete_frames
            ):
                self._logger.debug("market facts assessed", extra=record)
            else:
                self._logger.warning(
                    "market facts will be refused by the configured policy", extra=record
                )
        for timeframe in sorted(self._feed.timeframes):
            if timeframe not in windows:
                self._logger.warning(
                    "no market facts have arrived for this timeframe yet",
                    extra={"evaluated_at_utc": instant, "timeframe": timeframe.code},
                )

    def _report_containment(self, outcomes: Sequence[StrategyOutcome]) -> int:
        contained = 0
        for outcome in outcomes:
            if not outcome.contained:
                continue
            contained += 1
            self._logger.error(
                "strategy evaluation was contained and published as void",
                extra={
                    "strategy_id": str(outcome.identity.strategy_id),
                    "strategy_version": str(outcome.identity.strategy_version),
                    "contained_failure_type": outcome.failure_type,
                    "contained_failure_detail": outcome.failure_detail,
                },
            )
        return contained

    # ---------------------------------------------------------------- health

    def status(self, phase: str, *, resolution: Optional[str] = None) -> RuntimeStatus:
        return RuntimeStatus(
            phase=phase,
            environment=self._config.environment,
            instrument=str(self._instrument),
            started_at_utc=self._started_at_utc,
            updated_at_utc=self._wall_clock(),
            cycles_completed=self._cycles,
            envelopes_published=self._published,
            strategies_bound=len(self._strategies.atoms),
            chains_bound=len(self._strategies.chains),
            feed_passes_completed=self._passes,
            publication_sink=self._publisher.sink.description,
            last_evaluated_at_utc=self._last_evaluated_at_utc,
            contained_failures=self._contained,
            resolution=resolution,
        )

    def _write_status(self, phase: str, *, resolution: Optional[str] = None) -> None:
        write_status(self._runtime.status_file, self.status(phase, resolution=resolution))


def build_runtime(
    config: Optional[HeliosConfig] = None, *, publisher: Optional[StatePublisher] = None
) -> StrategyRuntime:
    """Assemble a service from validated configuration, failing loudly.

    Everything that can be decided without market facts is decided here:
    configuration completeness, the strategy handoff resolving, every package
    binding to an implementation this build ships, every chain definition being
    coherent, and the feed being internally consistent. A deployment that is
    wrong is wrong at startup, not three hours into a run.
    """
    resolved = load_config() if config is None else config
    runtime = resolved.runtime()
    bundle = load_handoff(runtime.package_dir)
    strategies = bind_strategies(bundle)
    feed = load_feed(
        runtime.feed_dir,
        instrument=runtime.instrument,
        accepted_schema_versions=resolved.accepted_hermes_schema_versions,
    )
    sink_publisher = (
        publisher if publisher is not None else StatePublisher(build_sink(resolved.publication()))
    )
    return StrategyRuntime(
        config=resolved,
        runtime=runtime,
        feed=feed,
        strategies=strategies,
        publisher=sink_publisher,
    )


def serve(argv: Optional[Sequence[str]] = None) -> int:
    """Run the service as a program. Returns the process result code.

    Installs handlers for SIGTERM and SIGINT so a container stop is a clean
    shutdown: the current evaluation finishes, the sink is flushed and closed,
    a final status document is written, and the reason is logged. Nothing is
    killed mid-payload, so a consumer never reads a half-written line.
    """
    del argv  # the service takes its whole configuration from the environment
    try:
        config = load_config()
    except HeliosError as error:
        # Logging is not configured yet: its level is itself configuration.
        # A configuration failure must still be legible, so it goes to stderr
        # as one line naming every problem found.
        sys.stderr.write(
            json.dumps(
                {
                    "level": "CRITICAL",
                    "logger": "helios.runtime",
                    "message": error.message,
                    "error_type": type(error).__name__,
                    "error_context": {
                        key: value for key, value in sorted(error.context.items())
                    },
                    "ts_utc": utc_now().isoformat().replace("+00:00", "Z"),
                },
                default=str,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        return 2

    configure_logging(config.log_level)
    logger = get_logger("runtime")
    try:
        runtime = build_runtime(config)
    except HeliosError as error:
        logger.critical(
            "refusing to start", exc_info=error, extra={"error_type": type(error).__name__}
        )
        return 2

    def handle(number: int, frame: Optional[FrameType]) -> None:
        del frame
        runtime.request_stop(f"received {signal.Signals(number).name}")

    for number in SHUTDOWN_SIGNALS:
        signal.signal(number, handle)

    try:
        runtime.run()
    except HeliosError as error:
        logger.critical(
            "runtime failed", exc_info=error, extra={"error_type": type(error).__name__}
        )
        return 1
    return 0
