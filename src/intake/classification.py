"""Slice 2: classify a message, then route on a human's confirmed decision.

The classifier only *proposes*; a human confirms every route. Confidence is
recorded for offline calibration and never used to auto-route. An existing
follow-up is re-authorised against the full ``sender -> client -> engagement ->
case`` chain immediately before anything is attached. Propose and confirm are
each idempotent per message; a conflicting second decision is rejected.
"""

from __future__ import annotations

import dataclasses
import logging

from intake.gate import authorise
from intake.ids import CaseId
from intake.model import (
    CLASSIFICATION_CONFIRMED,
    CLASSIFICATION_CORRECTED,
    CLASSIFICATION_PROPOSED,
    FOLLOWUP_AUTH_CHECKED,
    INTAKE_DECISION,
    AuditDraft,
    AuditEvent,
    AuthorisationDecision,
    ClarificationTask,
    ClassificationClass,
    ClassificationOutcome,
    ClassificationPayload,
    ClassificationProposal,
    DecisionKind,
    HumanDecision,
    IntakeMessage,
    MessageProjection,
    ReasonCode,
    TriageItem,
    intake_decision_draft,
)
from intake.protocols import (
    AuditLog,
    CaseRegistry,
    ClarificationQueue,
    Classifier,
    Clock,
    IdGenerator,
    TriageQueue,
)

_logger = logging.getLogger(__name__)


class ConflictingDecisionError(Exception):
    """Raised when a different decision is submitted for an already-confirmed message."""


@dataclasses.dataclass(frozen=True, slots=True)
class ProposalResult:
    """A proposal awaiting human confirmation, or a terminal triage at propose time."""

    proposal: ClassificationProposal | None
    triaged: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ConfirmResult:
    """The final outcome of a human-confirmed classification."""

    outcome: ClassificationOutcome
    case_id: CaseId | None = None


def _payload_of(proposal: ClassificationProposal | None) -> ClassificationPayload:
    if proposal is None:
        return ClassificationPayload(route_class=None, subtype=None, confidence=None)
    return ClassificationPayload(
        route_class=proposal.route_class,
        subtype=proposal.subtype,
        confidence=proposal.confidence,
        missing_fields=proposal.missing_fields,
        candidate_case_ids=proposal.candidate_case_ids,
    )


def _proposal_from_event(event: AuditEvent) -> ProposalResult:
    if event.outcome != "pending" or event.classification is None:
        return ProposalResult(proposal=None, triaged=True)
    payload = event.classification
    proposal = ClassificationProposal(
        route_class=payload.route_class or ClassificationClass.RESPONSE,
        subtype=payload.subtype,
        confidence=payload.confidence if payload.confidence is not None else 0.0,
        evidence_spans=(),
        candidate_case_ids=payload.candidate_case_ids,
        missing_fields=payload.missing_fields,
    )
    return ProposalResult(proposal=proposal, triaged=False)


def _confirm_from_event(event: AuditEvent) -> ConfirmResult:
    outcome = ClassificationOutcome(event.outcome)
    case_id = event.case_id if outcome is ClassificationOutcome.FOLLOWUP_AUTHORISED else None
    return ConfirmResult(outcome=outcome, case_id=case_id)


def _decision_signature(
    decision: HumanDecision,
) -> tuple[bool, ClassificationClass | None, object, CaseId | None, tuple[object, ...], str]:
    return (
        decision.triage,
        decision.confirmed_class,
        decision.confirmed_subtype,
        decision.selected_case_id,
        tuple(decision.confirmed_missing_fields),
        decision.reviewer_id,
    )


def _event_signature(
    event: AuditEvent,
) -> tuple[bool, ClassificationClass | None, object, CaseId | None, tuple[object, ...], str]:
    payload = event.classification
    return (
        event.outcome == ClassificationOutcome.TRIAGE.value,
        payload.route_class if payload is not None else None,
        payload.subtype if payload is not None else None,
        event.case_id,
        tuple(payload.missing_fields) if payload is not None else (),
        event.reviewer_id or "",
    )


class ClassificationService:
    """Coordinates propose -> human-confirm -> route, auditing each step."""

    def __init__(
        self,
        registry: CaseRegistry,
        audit: AuditLog,
        triage: TriageQueue,
        clarifications: ClarificationQueue,
        classifier: Classifier,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._triage = triage
        self._clarifications = clarifications
        self._classifier = classifier
        self._clock = clock
        self._ids = ids

    def propose(self, message: IntakeMessage, text: str) -> ProposalResult:
        """Run the classifier (once) to propose a route for the message.

        An exact claimed case id that fails the authorisation gate is triaged
        here and never reaches the classifier. A classifier failure or a
        mixed/safety-flagged proposal also routes to triage rather than guessing.

        Args:
            message: The inbound message reduced to routing inputs.
            text: The transient message text, used only to build the classifier
                projection; it is never persisted (only ``body_digest`` is).

        Returns:
            A proposal awaiting confirmation, or a terminal triage result.
        """
        proposed = self._audit.event_for(message.message_id, CLASSIFICATION_PROPOSED)
        if proposed is not None:
            return _proposal_from_event(proposed)
        if self._audit.event_for(message.message_id, INTAKE_DECISION) is not None:
            return ProposalResult(proposal=None, triaged=True)

        correlation_id = self._ids.new_id()
        claimed = message.claimed_case_ids
        if len(claimed) == 1:
            decision = authorise(
                self._registry, message.principal_id, claimed[0], self._clock.now()
            )
            if decision.kind is DecisionKind.TRIAGE:
                return self._triage_at_ingress(message, decision, correlation_id)

        projection = MessageProjection(
            message_id=message.message_id,
            principal_id=message.principal_id,
            text=text,
            candidate_case_ids=self._authorised_candidates(message),
        )
        try:
            proposal = self._classifier.classify(projection)
        except Exception:  # noqa: BLE001  pylint: disable=broad-exception-caught
            return self._triage_proposal(message, None, ReasonCode.CLASSIFIER_ERROR, correlation_id)

        if proposal.triage_reason is not None:
            return self._triage_proposal(message, proposal, proposal.triage_reason, correlation_id)

        self._record_proposal(message, proposal, "pending", None, correlation_id)
        _logger.info(
            "classification_proposed",
            extra={"message_id": message.message_id, "correlation_id": correlation_id},
        )
        return ProposalResult(proposal=proposal, triaged=False)

    def confirm(self, message: IntakeMessage, decision: HumanDecision) -> ConfirmResult:
        """Apply a reviewer's decision, routing and auditing the final outcome.

        Idempotent for a repeated identical decision; a conflicting second
        decision raises :class:`ConflictingDecisionError`.

        Args:
            message: The message being confirmed.
            decision: The reviewer's confirmed route (or explicit triage).

        Returns:
            The final classification outcome.

        Raises:
            ConflictingDecisionError: If a different decision was already confirmed.
        """
        confirmed = self._audit.event_for(message.message_id, CLASSIFICATION_CONFIRMED)
        if confirmed is not None:
            if _event_signature(confirmed) == _decision_signature(decision):
                return _confirm_from_event(confirmed)
            raise ConflictingDecisionError(message.message_id)

        proposed = self._audit.event_for(message.message_id, CLASSIFICATION_PROPOSED)
        correlation_id = proposed.correlation_id if proposed is not None else self._ids.new_id()
        if proposed is not None and self._is_correction(proposed, decision):
            self._record_correction(message, decision, correlation_id)

        outcome, case_id = self._route(message, decision, correlation_id)
        self._record_confirmation(message, decision, outcome, correlation_id)
        _logger.info(
            "classification_confirmed",
            extra={
                "message_id": message.message_id,
                "outcome": outcome.value,
                "reviewer_id": decision.reviewer_id,
                "correlation_id": correlation_id,
            },
        )
        return ConfirmResult(outcome=outcome, case_id=case_id)

    # --- routing -----------------------------------------------------------

    def _route(
        self, message: IntakeMessage, decision: HumanDecision, correlation_id: str
    ) -> tuple[ClassificationOutcome, CaseId | None]:
        if decision.triage or decision.confirmed_class is None:
            self._enqueue_triage(message, ReasonCode.MIXED_OR_FLAGGED)
            return ClassificationOutcome.TRIAGE, None
        if decision.confirmed_class is ClassificationClass.RESPONSE:
            return ClassificationOutcome.RESPONSE_READY, None
        if decision.confirmed_class is ClassificationClass.NEW_DELIVERABLE:
            return self._route_new_deliverable(message, decision)
        return self._route_followup(message, decision, correlation_id)

    def _route_new_deliverable(
        self, message: IntakeMessage, decision: HumanDecision
    ) -> tuple[ClassificationOutcome, CaseId | None]:
        if decision.confirmed_missing_fields:
            self._clarifications.enqueue(
                ClarificationTask(
                    task_id=self._ids.new_id(),
                    message_id=message.message_id,
                    subtype=decision.confirmed_subtype,
                    missing_fields=decision.confirmed_missing_fields,
                )
            )
            return ClassificationOutcome.AWAITING_INPUTS, None
        return ClassificationOutcome.NEW_DELIVERABLE_READY, None

    def _route_followup(
        self, message: IntakeMessage, decision: HumanDecision, correlation_id: str
    ) -> tuple[ClassificationOutcome, CaseId | None]:
        if decision.selected_case_id is None:
            self._enqueue_triage(message, ReasonCode.NO_CASE_SELECTED)
            return ClassificationOutcome.TRIAGE, None
        auth = authorise(
            self._registry, message.principal_id, decision.selected_case_id, self._clock.now()
        )
        self._audit.append(
            AuditDraft(
                action=FOLLOWUP_AUTH_CHECKED,
                outcome=auth.kind.value,
                subject_digest=message.body_digest,
                message_id=message.message_id,
                principal_id=message.principal_id,
                reason=auth.reason,
                detail=auth.detail,
                case_id=auth.case_id,
                correlation_id=correlation_id,
                reviewer_id=decision.reviewer_id,
            )
        )
        if auth.kind is DecisionKind.AUTHORISED:
            return ClassificationOutcome.FOLLOWUP_AUTHORISED, auth.case_id
        self._enqueue_triage(message, auth.reason or ReasonCode.NOT_AUTHORISED)
        return ClassificationOutcome.TRIAGE, None

    # --- triage helpers ----------------------------------------------------

    def _triage_at_ingress(
        self, message: IntakeMessage, decision: AuthorisationDecision, correlation_id: str
    ) -> ProposalResult:
        self._audit.append(intake_decision_draft(message, decision, correlation_id))
        self._enqueue_triage(message, decision.reason or ReasonCode.NOT_AUTHORISED)
        return ProposalResult(proposal=None, triaged=True)

    def _triage_proposal(
        self,
        message: IntakeMessage,
        proposal: ClassificationProposal | None,
        reason: ReasonCode,
        correlation_id: str,
    ) -> ProposalResult:
        self._record_proposal(message, proposal, "triage", reason, correlation_id)
        self._enqueue_triage(message, reason)
        return ProposalResult(proposal=None, triaged=True)

    def _enqueue_triage(self, message: IntakeMessage, reason: ReasonCode) -> None:
        self._triage.enqueue(
            TriageItem(
                triage_item_id=self._ids.new_id(),
                message_id=message.message_id,
                reason=reason,
                claimed_case_ids=message.claimed_case_ids,
            )
        )

    # --- candidates & audit ------------------------------------------------

    def _authorised_candidates(self, message: IntakeMessage) -> tuple[CaseId, ...]:
        now = self._clock.now()
        return tuple(
            case_id
            for case_id in message.claimed_case_ids
            if authorise(self._registry, message.principal_id, case_id, now).kind
            is DecisionKind.AUTHORISED
        )

    def _is_correction(self, proposed: AuditEvent, decision: HumanDecision) -> bool:
        payload = proposed.classification
        if payload is None:
            return False
        return (
            payload.route_class != decision.confirmed_class
            or payload.subtype != decision.confirmed_subtype
        )

    def _record_proposal(
        self,
        message: IntakeMessage,
        proposal: ClassificationProposal | None,
        outcome: str,
        reason: ReasonCode | None,
        correlation_id: str,
    ) -> None:
        self._audit.append(
            AuditDraft(
                action=CLASSIFICATION_PROPOSED,
                outcome=outcome,
                subject_digest=message.body_digest,
                message_id=message.message_id,
                principal_id=message.principal_id,
                reason=reason,
                detail=None,
                case_id=None,
                correlation_id=correlation_id,
                classification=_payload_of(proposal),
            )
        )

    def _record_correction(
        self, message: IntakeMessage, decision: HumanDecision, correlation_id: str
    ) -> None:
        self._audit.append(
            AuditDraft(
                action=CLASSIFICATION_CORRECTED,
                outcome="corrected",
                subject_digest=message.body_digest,
                message_id=message.message_id,
                principal_id=message.principal_id,
                reason=None,
                detail=None,
                case_id=decision.selected_case_id,
                correlation_id=correlation_id,
                reviewer_id=decision.reviewer_id,
                classification=ClassificationPayload(
                    route_class=decision.confirmed_class,
                    subtype=decision.confirmed_subtype,
                    confidence=None,
                    missing_fields=decision.confirmed_missing_fields,
                ),
            )
        )

    def _record_confirmation(
        self,
        message: IntakeMessage,
        decision: HumanDecision,
        outcome: ClassificationOutcome,
        correlation_id: str,
    ) -> None:
        self._audit.append(
            AuditDraft(
                action=CLASSIFICATION_CONFIRMED,
                outcome=outcome.value,
                subject_digest=message.body_digest,
                message_id=message.message_id,
                principal_id=message.principal_id,
                reason=None,
                detail=None,
                case_id=decision.selected_case_id,
                correlation_id=correlation_id,
                reviewer_id=decision.reviewer_id,
                classification=ClassificationPayload(
                    route_class=decision.confirmed_class,
                    subtype=decision.confirmed_subtype,
                    confidence=None,
                    missing_fields=decision.confirmed_missing_fields,
                ),
            )
        )
