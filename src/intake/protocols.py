"""Side-effecting boundary protocols, kept narrow for in-memory MVP impls.

The application service depends only on these; concrete persistence (in-memory
now, durable later) is swappable without touching the pure core.
"""
# Protocol method bodies are `...` stubs (required so pyright treats them as
# abstract): documenting their return for implementers is not redundant, the
# ellipsis is not unnecessary, and single-method protocols are intentional.
# pylint: disable=redundant-returns-doc,too-few-public-methods,unnecessary-ellipsis

from __future__ import annotations

import datetime as dt
from typing import Protocol

from intake.evidence_model import (
    AdapterResult,
    EvidenceQuery,
    FindingsLedger,
    SourceId,
)
from intake.ids import CaseId, EngagementId, MessageId, PrincipalId
from intake.model import (
    AuditDraft,
    AuditEvent,
    AuthorisationSnapshot,
    ClarificationTask,
    ClassificationProposal,
    MessageProjection,
    TriageItem,
)


class Clock(Protocol):
    """Source of the current time, injected for deterministic tests."""

    def now(self) -> dt.datetime:
        """Return the current instant.

        Returns:
            The current time as an aware ``datetime``.
        """
        ...


class IdGenerator(Protocol):
    """Source of unique identifiers, injected for deterministic tests."""

    def new_id(self) -> str:
        """Return a fresh unique identifier.

        Returns:
            A unique string id.
        """
        ...


class CaseRegistry(Protocol):
    """Read model for authorisation plus case registration."""

    def authorisation_snapshot(
        self, principal_id: PrincipalId, case_id: CaseId, as_of: dt.datetime
    ) -> AuthorisationSnapshot:
        """Resolve the relationship facts for an attach request.

        Args:
            principal_id: The authenticated principal making the request.
            case_id: The single case id claimed by the message.
            as_of: The instant at which authorisation validity is judged.

        Returns:
            The resolved snapshot for ``evaluate_authorisation``.
        """
        ...

    def register_case(self, engagement_id: EngagementId, case_id: CaseId) -> None:
        """Register a new case under an engagement.

        Args:
            engagement_id: The owning engagement.
            case_id: The new, unique, well-formed case id.

        Raises:
            KeyError: If the engagement is unknown.
            ValueError: If the case id is malformed or already registered.
        """
        ...

    def engagement_for_case(self, case_id: CaseId) -> EngagementId | None:
        """Return the engagement that owns a case, if registered.

        Args:
            case_id: The case to look up.

        Returns:
            The owning engagement id, or ``None`` if the case is unknown.
        """
        ...


class AuditLog(Protocol):
    """Append-only log of security-relevant events."""

    def append(self, draft: AuditDraft) -> AuditEvent:
        """Stamp a draft with id/sequence/time and append it.

        Args:
            draft: The event content to record.

        Returns:
            The appended, stamped event.
        """
        ...

    def event_for(self, message_id: MessageId, action: str) -> AuditEvent | None:
        """Return the most recent event for a message with the given action.

        Args:
            message_id: The message to look up.
            action: The audit action name to match.

        Returns:
            The matching event, or ``None`` if there is none.
        """
        ...

    def decision_for(self, message_id: MessageId) -> AuditEvent | None:
        """Return the recorded intake decision event for a message, if any.

        Args:
            message_id: The message to look up.

        Returns:
            The decision event, or ``None`` if the message is unseen.
        """
        ...

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        """Return all recorded events.

        Returns:
            Events in append order.
        """
        ...


class TriageQueue(Protocol):
    """Queue of messages awaiting human triage."""

    def enqueue(self, item: TriageItem) -> TriageItem:
        """Enqueue a triage item idempotently by message id.

        Args:
            item: The triage item to enqueue.

        Returns:
            The stored item (the existing one if already queued).
        """
        ...

    def has(self, message_id: MessageId) -> bool:
        """Return whether a message is already queued.

        Args:
            message_id: The message to check.

        Returns:
            ``True`` if already queued.
        """
        ...

    @property
    def items(self) -> tuple[TriageItem, ...]:
        """Return all queued items.

        Returns:
            Items in insertion order.
        """
        ...


class ClarificationQueue(Protocol):
    """Queue of routine 'need more inputs' tasks (distinct from security triage)."""

    def enqueue(self, task: ClarificationTask) -> ClarificationTask:
        """Enqueue a clarification task idempotently by message id.

        Args:
            task: The clarification task to enqueue.

        Returns:
            The stored task (the existing one if already queued).
        """
        ...

    @property
    def items(self) -> tuple[ClarificationTask, ...]:
        """Return all queued tasks.

        Returns:
            Tasks in insertion order.
        """
        ...


class Classifier(Protocol):
    """Proposes a route for a message. Advisory only — a human confirms it."""

    def classify(self, projection: MessageProjection) -> ClassificationProposal:
        """Propose a classification for a message projection.

        Args:
            projection: The minimum-necessary, sender-scoped message view.

        Returns:
            The proposed classification.
        """
        ...


class SourceAdapter(Protocol):
    """A single evidence source (planning/zoning, environmental, or utility/RTO)."""

    @property
    def source_id(self) -> SourceId:
        """Return the source this adapter answers for.

        Returns:
            The adapter's ``SourceId``.
        """
        ...

    def fetch(self, query: EvidenceQuery) -> AdapterResult:
        """Fetch evidence for a validated query.

        Args:
            query: The fully validated evidence query.

        Returns:
            The raw adapter result (the service normalises it fail-closed).
        """
        ...


class EvidenceStore(Protocol):
    """Immutable store of findings ledgers, separate from the audit log."""

    def save_ledger(self, ledger: FindingsLedger) -> FindingsLedger:
        """Persist a findings ledger.

        Args:
            ledger: The ledger to persist.

        Returns:
            The persisted ledger.
        """
        ...

    def ledger_for(self, case_id: CaseId, revision: int) -> FindingsLedger | None:
        """Return the ledger for a case manifest revision, if any.

        Args:
            case_id: The case to look up.
            revision: The manifest revision.

        Returns:
            The matching ledger, or ``None``.
        """
        ...

    @property
    def ledgers(self) -> tuple[FindingsLedger, ...]:
        """Return all persisted ledgers.

        Returns:
            Ledgers in save order.
        """
        ...
