"""In-memory implementations of the intake boundary protocols (MVP only).

These are append-only and hand back immutable values, which is sufficient for
the MVP and for deterministic tests. Durable immutability, tamper-evidence
(hash chaining), and write atomicity are deferred to the storage stage.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

from intake.ids import CaseId, ClientId, EngagementId, MessageId, PrincipalId
from intake.model import INTAKE_DECISION as _INTAKE_DECISION
from intake.model import (
    AuditDraft,
    AuditEvent,
    AuthorisationSnapshot,
    ClarificationTask,
    TriageItem,
)
from intake.protocols import Clock, IdGenerator

_CASE_ID_PATTERN = re.compile(r"^[A-Z]+-\d+$")


def is_well_formed_case_id(value: str) -> bool:
    r"""Return whether ``value`` matches the canonical case-id format.

    Args:
        value: The candidate case id.

    Returns:
        ``True`` if it matches ``^[A-Z]+-\d+$`` (e.g. ``CASE-1``).
    """
    return bool(_CASE_ID_PATTERN.match(value))


@dataclasses.dataclass(frozen=True, slots=True)
class _Principal:
    """An authenticated principal's client membership and revocation time."""

    client_id: ClientId
    revoked_at: dt.datetime | None


class InMemoryCaseRegistry:
    """In-memory case registry and authorisation read model."""

    def __init__(self) -> None:
        self._clients: set[ClientId] = set()
        self._engagements: dict[EngagementId, ClientId] = {}
        self._cases: dict[CaseId, EngagementId] = {}
        self._principals: dict[PrincipalId, _Principal] = {}

    def add_client(self, client_id: ClientId) -> None:
        """Register a client.

        Args:
            client_id: The client to add.
        """
        self._clients.add(client_id)

    def add_engagement(self, engagement_id: EngagementId, client_id: ClientId) -> None:
        """Register an engagement under a client.

        Args:
            engagement_id: The engagement to add.
            client_id: The owning client.

        Raises:
            KeyError: If the client is unknown.
        """
        if client_id not in self._clients:
            raise KeyError(client_id)
        self._engagements[engagement_id] = client_id

    def add_principal(
        self,
        principal_id: PrincipalId,
        client_id: ClientId,
        revoked_at: dt.datetime | None = None,
    ) -> None:
        """Register an authenticated principal as a member of a client.

        Args:
            principal_id: The principal to add.
            client_id: The client the principal belongs to.
            revoked_at: Optional instant from which authorisation is revoked.

        Raises:
            KeyError: If the client is unknown.
        """
        if client_id not in self._clients:
            raise KeyError(client_id)
        self._principals[principal_id] = _Principal(client_id, revoked_at)

    def register_case(self, engagement_id: EngagementId, case_id: CaseId) -> None:
        """Register a new case under an engagement.

        Args:
            engagement_id: The owning engagement.
            case_id: The new, unique, well-formed case id.

        Raises:
            KeyError: If the engagement is unknown.
            ValueError: If the case id is malformed or already registered.
        """
        if engagement_id not in self._engagements:
            raise KeyError(engagement_id)
        if not is_well_formed_case_id(case_id):
            raise ValueError(f"malformed case id: {case_id!r}")
        if case_id in self._cases:
            raise ValueError(f"case already registered: {case_id!r}")
        self._cases[case_id] = engagement_id

    def authorisation_snapshot(
        self, principal_id: PrincipalId, case_id: CaseId, as_of: dt.datetime
    ) -> AuthorisationSnapshot:
        """Resolve the relationship facts for an attach request.

        Args:
            principal_id: The authenticated principal making the request.
            case_id: The single case id claimed by the message.
            as_of: The instant at which authorisation validity is judged.

        Returns:
            The resolved snapshot for ``evaluate_authorisation``; an unknown or
            malformed case resolves to ``case_present=False`` with no leakage.
        """
        principal = self._principals.get(principal_id)
        principal_client = principal.client_id if principal is not None else None
        principal_active = principal is not None and (
            principal.revoked_at is None or as_of < principal.revoked_at
        )
        case_engagement = self._cases.get(case_id) if is_well_formed_case_id(case_id) else None
        case_present = case_engagement is not None
        engagement_client = (
            self._engagements.get(case_engagement) if case_engagement is not None else None
        )
        return AuthorisationSnapshot(
            case_id=case_id if case_present else None,
            principal_client=principal_client,
            case_present=case_present,
            case_engagement=case_engagement,
            engagement_client=engagement_client,
            principal_active=principal_active,
        )


class InMemoryAuditLog:
    """Append-only, in-memory audit log that stamps id/sequence/time."""

    def __init__(self, clock: Clock, ids: IdGenerator) -> None:
        self._clock = clock
        self._ids = ids
        self._events: list[AuditEvent] = []

    def append(self, draft: AuditDraft) -> AuditEvent:
        """Stamp a draft and append it.

        Args:
            draft: The event content to record.

        Returns:
            The appended, stamped event.
        """
        event = AuditEvent(
            event_id=self._ids.new_id(),
            sequence=len(self._events) + 1,
            at=self._clock.now(),
            action=draft.action,
            outcome=draft.outcome,
            subject_digest=draft.subject_digest,
            message_id=draft.message_id,
            principal_id=draft.principal_id,
            reason=draft.reason,
            detail=draft.detail,
            case_id=draft.case_id,
            correlation_id=draft.correlation_id,
            reviewer_id=draft.reviewer_id,
            classification=draft.classification,
        )
        self._events.append(event)
        return event

    def event_for(self, message_id: MessageId, action: str) -> AuditEvent | None:
        """Return the most recent event for a message with the given action.

        Args:
            message_id: The message to look up.
            action: The audit action name to match.

        Returns:
            The matching event, or ``None`` if there is none.
        """
        for event in reversed(self._events):
            if event.action == action and event.message_id == message_id:
                return event
        return None

    def decision_for(self, message_id: MessageId) -> AuditEvent | None:
        """Return the most recent intake decision event for a message.

        Args:
            message_id: The message to look up.

        Returns:
            The decision event, or ``None`` if the message is unseen.
        """
        return self.event_for(message_id, _INTAKE_DECISION)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        """Return all recorded events.

        Returns:
            Events in append order.
        """
        return tuple(self._events)


class InMemoryTriageQueue:
    """In-memory triage queue, idempotent by message id."""

    def __init__(self) -> None:
        self._items: list[TriageItem] = []
        self._seen: set[MessageId] = set()

    def enqueue(self, item: TriageItem) -> TriageItem:
        """Enqueue a triage item idempotently by message id.

        Args:
            item: The triage item to enqueue.

        Returns:
            The stored item (the existing one if already queued).
        """
        if item.message_id in self._seen:
            return next(e for e in self._items if e.message_id == item.message_id)
        self._seen.add(item.message_id)
        self._items.append(item)
        return item

    def has(self, message_id: MessageId) -> bool:
        """Return whether a message is already queued.

        Args:
            message_id: The message to check.

        Returns:
            ``True`` if already queued.
        """
        return message_id in self._seen

    @property
    def items(self) -> tuple[TriageItem, ...]:
        """Return all queued items.

        Returns:
            Items in insertion order.
        """
        return tuple(self._items)


class InMemoryClarificationQueue:
    """In-memory clarification queue, idempotent by message id."""

    def __init__(self) -> None:
        self._tasks: list[ClarificationTask] = []
        self._seen: set[MessageId] = set()

    def enqueue(self, task: ClarificationTask) -> ClarificationTask:
        """Enqueue a clarification task idempotently by message id.

        Args:
            task: The clarification task to enqueue.

        Returns:
            The stored task (the existing one if already queued).
        """
        if task.message_id in self._seen:
            return next(t for t in self._tasks if t.message_id == task.message_id)
        self._seen.add(task.message_id)
        self._tasks.append(task)
        return task

    @property
    def items(self) -> tuple[ClarificationTask, ...]:
        """Return all queued tasks.

        Returns:
            Tasks in insertion order.
        """
        return tuple(self._tasks)
