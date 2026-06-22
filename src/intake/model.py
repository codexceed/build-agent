"""Domain types for slice 1: messages, authorisation, audit, and triage.

All types are immutable value objects; mutation lives behind the boundary
protocols in :mod:`intake.protocols`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from intake.ids import CaseId, ClientId, EngagementId, MessageId, PrincipalId

# Audit action names, kept here (dependency-free) so the log and service agree.
INTAKE_DECISION = "intake.decision"
CASE_REGISTERED = "registry.case_registered"


class DecisionKind(enum.Enum):
    """Outcome of an intake authorisation decision."""

    AUTHORISED = "authorised"
    TRIAGE = "triage"


class ReasonCode(enum.Enum):
    """Client-safe reason a message was routed to triage."""

    NOT_AUTHORISED = "not_authorised"
    NO_CASE_REFERENCE = "no_case_reference"
    AMBIGUOUS_CASE_REFERENCE = "ambiguous_case_reference"


class DenialDetail(enum.Enum):
    """Internal, audit-only detail explaining an authorisation denial.

    Never returned to the client: it is recorded for human triage and metrics
    so a denial does not have to disclose case existence to the sender.
    """

    PRINCIPAL_UNKNOWN = "principal_unknown"
    CLIENT_MISMATCH = "client_mismatch"
    CASE_NOT_FOUND = "case_not_found"
    AUTHORISATION_REVOKED = "authorisation_revoked"


class TriageState(enum.Enum):
    """Lifecycle state of a triage item."""

    OPEN = "open"


@dataclasses.dataclass(frozen=True, slots=True)
class IntakeMessage:
    """An inbound message reduced to non-sensitive routing inputs.

    The authenticated principal is supplied by the upstream ingress gateway; no
    display name or address is carried, so none can be parsed for identity.
    """

    message_id: MessageId
    principal_id: PrincipalId
    claimed_case_ids: tuple[CaseId, ...]
    body_digest: str
    attachment_count: int


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorisationSnapshot:
    """Resolved relationship facts needed to evaluate one attach request."""

    case_id: CaseId | None
    principal_client: ClientId | None
    case_present: bool
    case_engagement: EngagementId | None
    engagement_client: ClientId | None
    principal_active: bool


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorisationDecision:
    """The outcome of evaluating a single attach request.

    On denial no case metadata is exposed: ``case_id`` is ``None`` and only a
    client-safe ``reason`` (plus audit-only ``detail``) is set.
    """

    kind: DecisionKind
    case_id: CaseId | None = None
    reason: ReasonCode | None = None
    detail: DenialDetail | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class AuditDraft:
    """The content of an audit event before the log stamps id/sequence/time."""

    action: str
    outcome: str
    subject_digest: str
    message_id: MessageId | None
    principal_id: PrincipalId | None
    reason: ReasonCode | None
    detail: DenialDetail | None
    case_id: CaseId | None
    correlation_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    """An append-only record of one security-relevant action.

    Stores only opaque ids and a content digest — never raw message bodies,
    addresses, or case contents.
    """

    event_id: str
    sequence: int
    at: dt.datetime
    action: str
    outcome: str
    subject_digest: str
    message_id: MessageId | None
    principal_id: PrincipalId | None
    reason: ReasonCode | None
    detail: DenialDetail | None
    case_id: CaseId | None
    correlation_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class TriageItem:
    """A message awaiting human triage, with the reason it was routed there."""

    triage_item_id: str
    message_id: MessageId
    reason: ReasonCode
    claimed_case_ids: tuple[CaseId, ...]
    state: TriageState = TriageState.OPEN
