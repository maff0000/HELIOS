"""The chain engine.

A chain composes the **normalised output envelopes** of atomic strategies. It
imports no atomic strategy, references none by module, and could not reach one
if it wanted to: its entire input is a set of
:class:`~helios.contracts.output.StrategyStateEnvelope` values plus its own
package. That is the PID's decoupling made structural — "atomic strategies do
not know other strategies exist", and the composition layer knows only their
published contract.

Nothing in this module contains a timeframe constant or a role-to-timeframe
mapping. Which timeframe fills ``CONTEXT`` is read from the package's own
``inputs`` block, per chain, exactly as the PID requires.

One evaluation is a pure function of ``(package, component envelopes,
evaluation instant, previous chain envelope)``. There is no hidden state, so
replaying the same ordered inputs reproduces the same envelopes byte for byte.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Sequence

from helios.clock import ensure_utc, to_iso8601_utc
from helios.composition.direction import apply_direction, resolve_chain_direction
from helios.composition.explanation import render_explanation
from helios.composition.outcomes import ChainAssessment, ComponentOutcome, ComponentReason
from helios.composition.primitives import evaluate_primitive
from helios.contracts._tokens import Instrument, ROLE_CONTEXT, ROLE_TRIGGER
from helios.contracts.identity import ChainId, StrategyIdentity
from helios.contracts.lifecycle import LifecycleTimestamps, advance_lifecycle
from helios.contracts.output import (
    ComponentProvenance,
    EnvelopeKind,
    InputFreshness,
    StrategyStateEnvelope,
    Validity,
)
from helios.contracts.state import (
    LIVE_STATES,
    RESOLVED_STATES,
    Direction,
    StrategyState,
    transition,
)
from helios.contracts.timeframe import Timeframe
from helios.determinism import deterministic_arithmetic
from helios.errors import ContractViolationError, StrategySpecError
from helios.observability.logging import get_logger
from helios.spec.model import (
    ChainComponent,
    ChainPrimitive,
    DirectionMode,
    DirectionRelationship,
    DirectionResolution,
    ExpiryMode,
    PackageKind,
    StrategyPackage,
)

#: Precision at which chain strength is published. Fixed so that two identical
#: evaluations serialise to identical bytes. The arithmetic that produces it
#: runs in :data:`helios.determinism.ARITHMETIC_CONTEXT` rather than whatever
#: context the host installed — see :func:`_strength`.
_STRENGTH_EXPONENT = Decimal("0.0001")

_LOGGER = get_logger("composition")


class ChainEngine:
    """Evaluates one chain package against its components' published states.

    Construction validates everything about the definition that can be decided
    without market facts. A definition that could only fail later, or fail
    ambiguously, fails here instead — loudly, naming the package.
    """

    def __init__(self, package: StrategyPackage) -> None:
        if package.kind is not PackageKind.CHAIN or package.chain is None:
            raise StrategySpecError(
                "the chain engine requires a CHAIN package",
                strategy_id=str(package.identity.strategy_id),
                kind=str(package.kind),
            )
        self._package = package
        self._chain = package.chain
        self._identity = StrategyIdentity(
            package.identity.strategy_id, package.identity.strategy_version
        )
        self._role_timeframes: Mapping[str, Timeframe] = package.role_timeframes
        self._ordered = self._canonical_order()
        self._check_direction_doctrine()
        self._check_context_is_not_finer_than_trigger()
        self._expiry_horizon = self._resolve_expiry_horizon()

    # ------------------------------------------------------------------ setup

    def _canonical_order(self) -> tuple[ChainComponent, ...]:
        """Components in the order this primitive reasons about them."""
        components = self._chain.components
        if self._chain.primitive is ChainPrimitive.SEQUENCE:
            return tuple(
                sorted(components, key=lambda item: item.sequence_index or 0)
            )
        if self._chain.primitive is ChainPrimitive.CONTEXT_TRIGGER:
            rank = {str(ROLE_CONTEXT): 0, str(ROLE_TRIGGER): 2}
            return tuple(
                item
                for _, item in sorted(
                    enumerate(components),
                    key=lambda pair: (rank.get(str(pair[1].role), 1), pair[0]),
                )
            )
        return components

    def _check_direction_doctrine(self) -> None:
        """The package-level relationship must not contradict the components'.

        ``direction.component_relationship`` states the chain's overall
        directional doctrine; each component states its own relationship to the
        chain. Left unchecked the two could disagree, and HELIOS would have to
        pick one — which is exactly the kind of silent invention the PID
        forbids.
        """
        spec = self._package.direction
        declared = [item.direction_relationship for item in self._ordered]
        overall = spec.component_relationship
        if overall is DirectionRelationship.SAME and any(
            item is DirectionRelationship.OPPOSITE for item in declared
        ):
            raise StrategySpecError(
                "the chain declares an overall SAME direction relationship but a "
                "component declares OPPOSITE; the two statements contradict",
                strategy_id=str(self._identity.strategy_id),
            )
        if overall is DirectionRelationship.OPPOSITE and not any(
            item is DirectionRelationship.OPPOSITE for item in declared
        ):
            raise StrategySpecError(
                "the chain declares an overall OPPOSITE direction relationship but "
                "no component declares OPPOSITE",
                strategy_id=str(self._identity.strategy_id),
            )
        if (
            spec.mode is DirectionMode.DIRECTIONAL
            and spec.resolution is DirectionResolution.FROM_COMPONENTS
            and not any(item is DirectionRelationship.SAME for item in declared)
        ):
            raise StrategySpecError(
                "a DIRECTIONAL chain resolving direction from its components must "
                "declare at least one component with direction_relationship SAME; "
                "without one there is nothing for the chain to adopt its direction "
                "from, and HELIOS will not invent a bias",
                strategy_id=str(self._identity.strategy_id),
            )

    def _check_context_is_not_finer_than_trigger(self) -> None:
        """A CONTEXT_TRIGGER context must not sit on a finer timeframe.

        This reads the package's **own** role bindings. It hard-codes no
        timeframe: a chain may put its context on D1, H4 or M15 as it pleases,
        so long as the context is not finer-grained than the thing it frames.
        """
        if self._chain.primitive is not ChainPrimitive.CONTEXT_TRIGGER:
            return
        context = self._role_timeframes.get(str(ROLE_CONTEXT))
        trigger = self._role_timeframes.get(str(ROLE_TRIGGER))
        if context is None or trigger is None:
            return
        if context < trigger:
            raise StrategySpecError(
                "a CONTEXT_TRIGGER chain binds its CONTEXT role to a finer timeframe "
                "than its TRIGGER role; a context cannot frame something slower "
                "than itself",
                strategy_id=str(self._identity.strategy_id),
                context_timeframe=context.code,
                trigger_timeframe=trigger.code,
            )

    def _resolve_expiry_horizon(self) -> Optional[timedelta]:
        """How long a matched chain occurrence stays valid.

        ``NEVER`` — open-ended. ``DURATION`` — exactly as declared.
        ``FRAMES`` — counted in frames of the **finest timeframe the package
        binds**, because that is the resolution at which the chain concludes:
        a CONTEXT_TRIGGER chain's trigger is by definition on its finest
        timeframe, and "6 frames" for such a chain means six of those. The
        timeframe comes from the package's own bindings; there is no default.
        A ``FRAMES`` chain that binds no timeframe at all is refused rather
        than guessed at.
        """
        expiry = self._package.expiry
        if expiry.mode is ExpiryMode.NEVER:
            return None
        if expiry.mode is ExpiryMode.DURATION:
            return timedelta(seconds=int(expiry.duration_seconds or 0))
        bound = sorted(self._role_timeframes.values())
        if not bound:
            raise StrategySpecError(
                "the chain declares a FRAMES expiry but binds no input timeframe, "
                "so 'frames of what?' has no answer",
                strategy_id=str(self._identity.strategy_id),
            )
        return bound[0].duration * int(expiry.frames or 0)

    # --------------------------------------------------------------- accessors

    @property
    def identity(self) -> StrategyIdentity:
        return self._identity

    @property
    def package(self) -> StrategyPackage:
        return self._package

    @property
    def primitive(self) -> ChainPrimitive:
        return self._chain.primitive

    @property
    def ordered_components(self) -> tuple[ChainComponent, ...]:
        return self._ordered

    @property
    def expiry_horizon(self) -> Optional[timedelta]:
        return self._expiry_horizon

    def component_timeframe(self, component: ChainComponent) -> Optional[Timeframe]:
        """The timeframe this package binds to the component's role, if any."""
        if component.role is None:
            return None
        return self._role_timeframes.get(str(component.role))

    # -------------------------------------------------------------- assessment

    def assess(
        self,
        *,
        instrument: Instrument | str,
        evaluated_at_utc: datetime,
        components: Iterable[StrategyStateEnvelope] = (),
        pinned_direction: Optional[Direction] = None,
    ) -> ChainAssessment:
        """Does the composed condition hold right now, and why (not)?

        Deliberately free of history: this answers only "does it hold", so it
        can be tested on its own. Everything temporal — persistence, expiry,
        lifecycle — is applied by :meth:`evaluate` on top of this verdict.

        ``pinned_direction`` fixes the direction rather than re-resolving it.
        :meth:`evaluate` pins the direction a live chain matched in, so a
        component that flips its bias voids the chain instead of silently
        re-publishing it in the other direction.
        """
        subject = _coerce_instrument(instrument)
        now = ensure_utc(evaluated_at_utc, field="evaluated_at_utc")
        supplied = self._index(components, subject)

        outcomes = tuple(
            self._assess_component(component, supplied, now) for component in self._ordered
        )
        direction = (
            pinned_direction
            if pinned_direction is not None
            else resolve_chain_direction(self._package.direction, outcomes)
        )
        outcomes = apply_direction(outcomes, direction)
        result = evaluate_primitive(self._chain, outcomes)
        return ChainAssessment(
            satisfied=result.satisfied,
            direction=direction,
            outcomes=result.outcomes,
            chain_reasons=result.reasons,
        )

    def _index(
        self, components: Iterable[StrategyStateEnvelope], subject: Instrument
    ) -> Mapping[str, Mapping[str, StrategyStateEnvelope]]:
        """Index the supplied envelopes by ``strategy_id`` then version.

        Envelopes for strategies this chain does not declare are ignored: a
        runtime may reasonably hand the engine every atomic output it has.
        Everything the chain *does* declare is checked hard.
        """
        declared = {str(component.strategy_id) for component in self._ordered}
        indexed: dict[str, dict[str, StrategyStateEnvelope]] = {}
        for envelope in components:
            if not isinstance(envelope, StrategyStateEnvelope):
                raise ContractViolationError(
                    "a chain component state must be a StrategyStateEnvelope",
                    received_type=type(envelope).__name__,
                )
            name = str(envelope.strategy_id)
            if name not in declared:
                continue
            if envelope.kind is not EnvelopeKind.ATOMIC:
                raise StrategySpecError(
                    "v1 chains compose atomic strategies directly; a composite was "
                    "supplied as a component. Recursive chain-of-chain composition "
                    "requires explicit architecture authority",
                    chain_id=str(self._identity.strategy_id),
                    component=name,
                )
            if envelope.instrument != subject:
                raise ContractViolationError(
                    "a chain component state is for a different instrument",
                    chain_id=str(self._identity.strategy_id),
                    component=name,
                    expected=str(subject),
                    received=str(envelope.instrument),
                )
            by_version = indexed.setdefault(name, {})
            version = str(envelope.strategy_version)
            if version in by_version:
                raise ContractViolationError(
                    "two states were supplied for one component identity; the "
                    "chain's provenance would be ambiguous",
                    chain_id=str(self._identity.strategy_id),
                    component=f"{name}@{version}",
                )
            by_version[version] = envelope
        return indexed

    def _assess_component(
        self,
        component: ChainComponent,
        supplied: Mapping[str, Mapping[str, StrategyStateEnvelope]],
        now: datetime,
    ) -> ComponentOutcome:
        """Whether one component holds on its own terms.

        Checked in a fixed order, each check a stated rule:

        1. a state was supplied at all;
        2. it was supplied for the exact version the chain declares — a chain
           must be able to say precisely which component versions produced its
           state, so a different version is a different component;
        3. every market fact behind that state was fresh — a state resting on
           a stale fact cannot be counted, whatever it says;
        4. the component's own declared validity had not lapsed — this is
           component persistence: a component's match continues to count for
           exactly as long as the component itself says it does;
        5. the state is one the chain declared as satisfying, with ``INVALID``
           and ``EXPIRED`` called out separately from an ordinary mismatch.
        """
        declared = StrategyIdentity(component.strategy_id, component.strategy_version)
        timeframe = self.component_timeframe(component)
        base = dict(
            declared=declared,
            role=component.role,
            timeframe=timeframe,
            sequence_index=component.sequence_index,
            relationship=component.direction_relationship,
            required_states=component.required_states,
        )
        versions = supplied.get(str(component.strategy_id), {})
        envelope = versions.get(str(component.strategy_version))

        if envelope is None:
            if versions:
                return ComponentOutcome(
                    **base,
                    envelope=None,
                    satisfied=False,
                    reason=ComponentReason.VERSION_MISMATCH,
                    detail=(
                        f"the chain declares version {component.strategy_version} but "
                        f"state was supplied only for version(s) "
                        f"{', '.join(sorted(versions))}"
                    ),
                )
            return ComponentOutcome(
                **base,
                envelope=None,
                satisfied=False,
                reason=ComponentReason.NOT_SUPPLIED,
                detail="no state was supplied for this component in this evaluation",
            )

        stale = [item for item in envelope.inputs if not item.is_fresh]
        if stale:
            return ComponentOutcome(
                **base,
                envelope=envelope,
                satisfied=False,
                reason=ComponentReason.STALE_COMPONENT_INPUT,
                detail=(
                    "the component's state rests on "
                    f"{len(stale)} stale market fact(s), the oldest at "
                    f"{max(item.age_seconds for item in stale)}s against a limit of "
                    f"{min(item.max_age_seconds for item in stale)}s"
                ),
            )

        valid_until = envelope.validity.valid_until_utc
        if valid_until is not None and now > valid_until:
            return ComponentOutcome(
                **base,
                envelope=envelope,
                satisfied=False,
                reason=ComponentReason.COMPONENT_VALIDITY_LAPSED,
                detail=(
                    f"the component's own validity ended at "
                    f"{to_iso8601_utc(valid_until)}, before this evaluation at "
                    f"{to_iso8601_utc(now)}"
                ),
            )

        if envelope.state not in component.required_states:
            if envelope.state is StrategyState.INVALID:
                reason = ComponentReason.COMPONENT_INVALIDATED
                detail = (
                    "the component declared its own match void"
                    + (
                        f": {envelope.validity.reason}"
                        if envelope.validity.reason
                        else ""
                    )
                )
            elif envelope.state is StrategyState.EXPIRED:
                reason = ComponentReason.COMPONENT_AGED_OUT
                detail = (
                    "the component's match aged out"
                    + (
                        f": {envelope.validity.reason}"
                        if envelope.validity.reason
                        else ""
                    )
                )
            else:
                reason = ComponentReason.STATE_NOT_REQUIRED
                detail = (
                    f"state {envelope.state} is not one of the required states "
                    f"{', '.join(str(item) for item in component.required_states)}"
                )
            return ComponentOutcome(
                **base, envelope=envelope, satisfied=False, reason=reason, detail=detail
            )

        return ComponentOutcome(
            **base,
            envelope=envelope,
            satisfied=True,
            reason=ComponentReason.SATISFIED,
            detail=(
                f"state {envelope.state} is one of the required states "
                f"{', '.join(str(item) for item in component.required_states)}"
            ),
        )

    # -------------------------------------------------------------- evaluation

    def evaluate(
        self,
        *,
        instrument: Instrument | str,
        evaluated_at_utc: datetime,
        components: Sequence[StrategyStateEnvelope] = (),
        previous: Optional[StrategyStateEnvelope] = None,
    ) -> StrategyStateEnvelope:
        """Publish this chain's normalised state for one evaluation.

        Runs inside the engine's fixed decimal context, so the chain's
        derived measures do not depend on whatever decimal context the host
        process installed. See :mod:`helios.determinism`.
        """
        with deterministic_arithmetic():
            subject = _coerce_instrument(instrument)
            now = ensure_utc(evaluated_at_utc, field="evaluated_at_utc")
            self._check_previous(previous, subject)

            carry_in = previous.state if previous is not None else StrategyState.DORMANT
            pinned = (
                previous.direction
                if previous is not None and previous.state in LIVE_STATES
                else None
            )
            assessment = self.assess(
                instrument=subject,
                evaluated_at_utc=now,
                components=components,
                pinned_direction=pinned,
            )

            streak_in = _previous_streak(previous)
            state, streak, note = self._resolve_state(
                carry_in=carry_in,
                streak=streak_in,
                assessment=assessment,
                previous=previous,
                now=now,
            )
            # Proves the published sequence never leaves the documented table.
            transition(carry_in, state, at_utc=now, reason=note)

            lifecycle = advance_lifecycle(_previous_lifecycle(previous), state, now)
            validity = self._validity(state, lifecycle, previous, now, note)
            explanation = render_explanation(
                identity=self._identity,
                primitive=self._chain.primitive,
                instrument=str(subject),
                state=state,
                assessment=assessment,
                resolution_note=note,
            )
            envelope = StrategyStateEnvelope.with_lifecycle(
                lifecycle,
                kind=EnvelopeKind.CHAIN,
                strategy_id=self._identity.strategy_id,
                strategy_version=self._identity.strategy_version,
                chain_id=ChainId(str(self._identity.strategy_id)),
                chain_version=self._identity.strategy_version,
                instrument=subject,
                state=state,
                direction=assessment.direction,
                strength=_strength(assessment),
                evidence=self._evidence(assessment, streak),
                explanation=explanation,
                validity=validity,
                components=self._provenance(assessment, now),
                inputs=_aggregate_inputs(assessment),
            )
            _LOGGER.debug(
                "chain evaluated",
                extra={
                    "chain_id": str(self._identity.strategy_id),
                    "chain_version": str(self._identity.strategy_version),
                    "chain_primitive": str(self._chain.primitive),
                    "chain_state": str(state),
                    "chain_direction": str(assessment.direction),
                    "components_satisfied": assessment.satisfied_count,
                    "components_declared": assessment.declared_count,
                    "evaluated_at_utc": now,
                    "subject_instrument": str(subject),
                },
            )
            return envelope

    def _check_previous(
        self, previous: Optional[StrategyStateEnvelope], subject: Instrument
    ) -> None:
        """A chain resumes only from its own last envelope.

        One strategy cannot mutate or inherit another's state. Handing this
        chain somebody else's envelope is a wiring fault and fails loudly
        rather than producing a plausible-looking lifecycle.
        """
        if previous is None:
            return
        if previous.kind is not EnvelopeKind.CHAIN:
            raise ContractViolationError(
                "a chain resumes only from a CHAIN envelope",
                chain_id=str(self._identity.strategy_id),
                received_kind=str(previous.kind),
            )
        if (
            str(previous.strategy_id) != str(self._identity.strategy_id)
            or previous.strategy_version != self._identity.strategy_version
        ):
            raise ContractViolationError(
                "a chain resumes only from its own previous envelope",
                chain_id=self._identity.canonical,
                received=f"{previous.strategy_id}@{previous.strategy_version}",
            )
        if previous.instrument != subject:
            raise ContractViolationError(
                "the previous envelope is for a different instrument",
                chain_id=self._identity.canonical,
                expected=str(subject),
                received=str(previous.instrument),
            )

    def _resolve_state(
        self,
        *,
        carry_in: StrategyState,
        streak: int,
        assessment: ChainAssessment,
        previous: Optional[StrategyStateEnvelope],
        now: datetime,
    ) -> tuple[StrategyState, int, str]:
        """The published state, the persistence count, and why.

        The rules, in the order they are applied:

        * **A resolved occurrence latches.** ``INVALID``/``EXPIRED`` are
          republished while the composed condition still holds, and the chain
          rearms to ``DORMANT`` once it clears. A resolved chain therefore
          cannot re-fire on the evidence that already resolved it, and the
          published sequence never violates the state model's transition table.
        * **A live chain checks time before condition.** If its declared
          validity has passed it is ``EXPIRED`` — time ran out — even if the
          condition also stopped holding. Nothing broke, so ``INVALID`` would
          misreport it.
        * **A live chain whose condition stops holding is ``INVALID``.** The
          state model forbids falling back to ``DORMANT``/``FORMING``: an
          occurrence that started must resolve explicitly.
        * **A chain matches only after ``min_matched_frames`` consecutive
          satisfied evaluations.** That is chain-level persistence. Below the
          threshold it publishes ``FORMING`` and says how far along it is.
        * **A partially satisfied chain is ``FORMING``**, and one with nothing
          holding is ``DORMANT``.
        """
        if carry_in in RESOLVED_STATES:
            if assessment.satisfied:
                return (
                    carry_in,
                    0,
                    f"this occurrence already resolved as {carry_in}; the chain "
                    "rearms once the composed condition clears, so it cannot "
                    "re-match on the evidence that resolved it",
                )
            return (
                StrategyState.DORMANT,
                0,
                f"the previous occurrence resolved as {carry_in} and the composed "
                "condition no longer holds, so the chain has rearmed",
            )

        if carry_in in LIVE_STATES:
            valid_until = previous.validity.valid_until_utc if previous is not None else None
            if valid_until is not None and now > valid_until:
                return (
                    StrategyState.EXPIRED,
                    0,
                    "the chain's declared validity ended at "
                    f"{to_iso8601_utc(valid_until)} and this evaluation is later; "
                    "time ran out rather than anything breaking",
                )
            if not assessment.satisfied:
                culprit = assessment.first_unsatisfied
                if culprit is not None:
                    detail = culprit.describe()
                elif assessment.chain_reasons:
                    detail = "; ".join(assessment.chain_reasons)
                else:  # pragma: no cover - a primitive always states a reason
                    detail = "the composed condition no longer holds"
                return (
                    StrategyState.INVALID,
                    0,
                    "a live chain whose composed condition stops holding is void — "
                    + detail,
                )
            weakening, why = self._weakening(assessment, previous)
            if weakening:
                return StrategyState.WEAKENING, streak, why
            return (
                StrategyState.ACTIVE,
                streak,
                "the composed condition still holds and every component remains "
                "compatible",
            )

        required = self._package.persistence.min_matched_frames
        if assessment.satisfied:
            advanced = (streak if carry_in is StrategyState.FORMING else 0) + 1
            if advanced >= required:
                return (
                    StrategyState.MATCHED,
                    advanced,
                    f"the composed condition held on {advanced} consecutive "
                    f"evaluation(s), meeting the declared min_matched_frames of "
                    f"{required}",
                )
            return (
                StrategyState.FORMING,
                advanced,
                f"the composed condition has held on {advanced} of the {required} "
                "consecutive evaluations the package requires before a match is "
                "published",
            )
        if assessment.satisfied_count:
            return (
                StrategyState.FORMING,
                0,
                f"{assessment.satisfied_count} of {assessment.declared_count} "
                "components hold; the composed condition does not",
            )
        return StrategyState.DORMANT, 0, "no declared component holds"

    def _weakening(
        self, assessment: ChainAssessment, previous: Optional[StrategyStateEnvelope]
    ) -> tuple[bool, str]:
        """Is a still-holding chain degrading?

        Only when the package enables it. Two declared measures, both derived
        from published component state rather than invented:

        1. a contributing component publishes ``WEAKENING`` itself;
        2. the chain's own strength — the share of declared components that
           hold — fell since the previous evaluation. This is the measure that
           makes ``ANY`` meaningful: two of three components holding is a
           weaker chain than three of three.
        """
        if not self._package.persistence.weakening_enabled:
            return False, ""
        degrading = [
            outcome
            for outcome in assessment.outcomes
            if outcome.satisfied and outcome.observed_state is StrategyState.WEAKENING
        ]
        if degrading:
            return True, (
                "the composed condition still holds but "
                + ", ".join(outcome.label for outcome in degrading)
                + " reports WEAKENING"
            )
        if previous is not None and previous.strength is not None:
            current = _strength(assessment)
            if current < previous.strength:
                return True, (
                    "the composed condition still holds but fewer components do: "
                    f"strength fell from {previous.strength} to {current}"
                )
        return False, ""

    def _validity(
        self,
        state: StrategyState,
        lifecycle: LifecycleTimestamps,
        previous: Optional[StrategyStateEnvelope],
        now: datetime,
        note: str,
    ) -> Validity:
        """The temporal validity published with this state."""
        if state in LIVE_STATES:
            valid_from = lifecycle.first_matched_at_utc
            valid_until = None
            if self._expiry_horizon is not None and valid_from is not None:
                valid_until = valid_from + self._expiry_horizon
            return Validity(valid_from_utc=valid_from, valid_until_utc=valid_until)
        if state in RESOLVED_STATES:
            if previous is not None and previous.state is state:
                # The occurrence resolved on an earlier evaluation; its window
                # is a fact of that evaluation and must not drift.
                return previous.validity
            ended = None
            if state is StrategyState.EXPIRED and previous is not None:
                ended = previous.validity.valid_until_utc
            return Validity(
                valid_from_utc=lifecycle.first_matched_at_utc,
                valid_until_utc=ended if ended is not None else now,
                reason=note,
            )
        return Validity()

    def _evidence(self, assessment: ChainAssessment, streak: int) -> dict[str, object]:
        """Flat, scalar evidence for this evaluation.

        ``satisfied_evaluations`` is published so the next evaluation can
        resume the persistence count from the envelope alone. That is what
        makes replay a pure function of the published record: the engine holds
        no hidden counter between evaluations.
        """
        evidence: dict[str, object] = {
            "primitive": str(self._chain.primitive),
            "components_declared": assessment.declared_count,
            "components_satisfied": assessment.satisfied_count,
            "satisfied_evaluations": streak,
            "min_matched_frames": self._package.persistence.min_matched_frames,
            "resolved_direction": str(assessment.direction),
            "expiry_mode": str(self._package.expiry.mode),
        }
        if self._expiry_horizon is not None:
            evidence["validity_seconds"] = int(self._expiry_horizon.total_seconds())
        if self._chain.ordering_window_seconds is not None:
            evidence["ordering_window_seconds"] = self._chain.ordering_window_seconds
        culprit = assessment.first_unsatisfied
        if culprit is not None:
            evidence["first_unsatisfied_component"] = culprit.declared.canonical
            evidence["first_unsatisfied_reason"] = str(culprit.reason)
        return evidence

    def _provenance(
        self, assessment: ChainAssessment, now: datetime
    ) -> tuple[ComponentProvenance, ...]:
        return tuple(
            outcome.provenance(evaluated_at_utc=now) for outcome in assessment.outcomes
        )


# ------------------------------------------------------------------- helpers


def _coerce_instrument(value: Instrument | str) -> Instrument:
    return value if isinstance(value, Instrument) else Instrument(str(value))


def _previous_lifecycle(
    previous: Optional[StrategyStateEnvelope],
) -> Optional[LifecycleTimestamps]:
    if previous is None:
        return None
    return LifecycleTimestamps(
        last_evaluated_at_utc=previous.last_evaluated_at_utc,
        first_matched_at_utc=previous.first_matched_at_utc,
        last_matched_at_utc=previous.last_matched_at_utc,
        active_since_utc=previous.active_since_utc,
    )


def _previous_streak(previous: Optional[StrategyStateEnvelope]) -> int:
    if previous is None:
        return 0
    value = previous.evidence.get("satisfied_evaluations")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _strength(assessment: ChainAssessment) -> Decimal:
    """The share of declared components that hold, as an exact decimal.

    Computed in the engine's fixed decimal context. Under the AMBIENT context
    this is not merely a question of which digits come out: ``3/4`` quantised
    to four places needs four significant digits, so under an installed
    ``prec=3`` context the quantisation raises ``InvalidOperation`` and a chain
    that should have published ``0.7500`` publishes nothing at all. A published
    state must not depend on decimal state HELIOS does not control.
    """
    if not assessment.declared_count:  # pragma: no cover - spec requires two
        return Decimal(0)
    with deterministic_arithmetic():
        share = Decimal(assessment.satisfied_count) / Decimal(assessment.declared_count)
        return share.quantize(_STRENGTH_EXPONENT)


def _aggregate_inputs(assessment: ChainAssessment) -> tuple[InputFreshness, ...]:
    """Every market fact behind every component, deduplicated and ordered.

    The chain republishes each component's freshness record as the component
    published it, including the component's own semantic role naming. HELIOS
    does not rewrite one strategy's provenance in another's vocabulary.
    """
    seen: dict[tuple, InputFreshness] = {}
    for outcome in assessment.outcomes:
        envelope = outcome.envelope
        if envelope is None:
            continue
        for item in envelope.inputs:
            key = (
                str(item.instrument),
                item.timeframe.code,
                str(item.semantic_role) if item.semantic_role is not None else "",
                item.frame_timestamp_utc,
                str(item.source),
                item.schema_version,
                item.age_seconds,
                item.max_age_seconds,
                item.is_fresh,
                item.is_complete,
            )
            seen.setdefault(key, item)
    return tuple(seen[key] for key in sorted(seen))


__all__ = ["ChainEngine"]
