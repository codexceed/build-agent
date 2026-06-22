"""Application boundary: coordinate registry read, decision, audit, and triage.

The service holds no policy of its own — authorisation is decided by the pure
:func:`intake.authorisation.evaluate_authorisation`. Its job is sequencing and
observability: never expose a case until authorised, audit every decision and
mutation, and triage anything not cleanly authorised.
"""

from __future__ import annotations

import hashlib
import logging

from intake.gate import authorise
from intake.ids import CaseId, EngagementId
from intake.model import (
    CASE_REGISTERED,
    AuditDraft,
    AuditEvent,
    AuthorisationDecision,
    DecisionKind,
    IntakeMessage,
    ReasonCode,
    TriageItem,
    intake_decision_draft,
)
from intake.protocols import AuditLog, CaseRegistry, Clock, IdGenerator, TriageQueue

_logger = logging.getLogger(__name__)


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision_from_event(event: AuditEvent) -> AuthorisationDecision:
    return AuthorisationDecision(
        kind=DecisionKind(event.outcome),
        case_id=event.case_id,
        reason=event.reason,
        detail=event.detail,
    )


class IntakeService:
    """Coordinates authorised intake: decide, audit, and triage."""

    def __init__(
        self,
        registry: CaseRegistry,
        audit: AuditLog,
        triage: TriageQueue,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._triage = triage
        self._clock = clock
        self._ids = ids

    def register_case(self, engagement_id: EngagementId, case_id: CaseId) -> None:
        """Register a case and record the mutation in the audit log.

        Args:
            engagement_id: The owning engagement.
            case_id: The new, unique case id.

        Raises:
            KeyError: If the engagement is unknown.
            ValueError: If the case id is malformed or already registered.
        """
        self._registry.register_case(engagement_id, case_id)
        correlation_id = self._ids.new_id()
        self._audit.append(
            AuditDraft(
                action=CASE_REGISTERED,
                outcome="registered",
                subject_digest=_digest(f"{engagement_id}:{case_id}"),
                message_id=None,
                principal_id=None,
                reason=None,
                detail=None,
                case_id=case_id,
                correlation_id=correlation_id,
            )
        )
        _logger.info(
            "case_registered",
            extra={
                "case_id": case_id,
                "engagement_id": engagement_id,
                "correlation_id": correlation_id,
            },
        )

    def handle_intake(self, message: IntakeMessage) -> AuthorisationDecision:
        """Authorise or triage an inbound message, recording the outcome.

        Idempotent by message id: a previously handled message returns its
        original decision without writing new audit or triage records.

        Args:
            message: The inbound message reduced to routing inputs.

        Returns:
            The authorisation decision (authorised or triage).
        """
        prior = self._audit.decision_for(message.message_id)
        if prior is not None:
            return _decision_from_event(prior)

        decision = self._decide(message)
        correlation_id = self._ids.new_id()
        self._audit.append(intake_decision_draft(message, decision, correlation_id))
        if decision.kind is DecisionKind.TRIAGE and decision.reason is not None:
            self._triage.enqueue(
                TriageItem(
                    triage_item_id=self._ids.new_id(),
                    message_id=message.message_id,
                    reason=decision.reason,
                    claimed_case_ids=message.claimed_case_ids,
                )
            )
        _logger.info(
            "intake_decision",
            extra={
                "message_id": message.message_id,
                "outcome": decision.kind.value,
                "reason": decision.reason.value if decision.reason is not None else None,
                "correlation_id": correlation_id,
            },
        )
        return decision

    def _decide(self, message: IntakeMessage) -> AuthorisationDecision:
        claimed = message.claimed_case_ids
        if len(claimed) == 0:
            return AuthorisationDecision(
                kind=DecisionKind.TRIAGE, reason=ReasonCode.NO_CASE_REFERENCE
            )
        if len(claimed) > 1:
            return AuthorisationDecision(
                kind=DecisionKind.TRIAGE, reason=ReasonCode.AMBIGUOUS_CASE_REFERENCE
            )
        return authorise(self._registry, message.principal_id, claimed[0], self._clock.now())
