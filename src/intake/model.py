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
CLASSIFICATION_PROPOSED = "classification.proposed"
CLASSIFICATION_CONFIRMED = "classification.confirmed"
CLASSIFICATION_CORRECTED = "classification.corrected"
FOLLOWUP_AUTH_CHECKED = "followup.authorisation_checked"


class DecisionKind(enum.Enum):
    """Outcome of an intake authorisation decision."""

    AUTHORISED = "authorised"
    TRIAGE = "triage"


class ReasonCode(enum.Enum):
    """Client-safe reason a message was routed to triage."""

    NOT_AUTHORISED = "not_authorised"
    NO_CASE_REFERENCE = "no_case_reference"
    AMBIGUOUS_CASE_REFERENCE = "ambiguous_case_reference"
    MIXED_OR_FLAGGED = "mixed_or_flagged"
    CLASSIFIER_ERROR = "classifier_error"
    NO_CASE_SELECTED = "no_case_selected"


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
    reviewer_id: str | None = None
    classification: ClassificationPayload | None = None


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
    reviewer_id: str | None = None
    classification: ClassificationPayload | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TriageItem:
    """A message awaiting human triage, with the reason it was routed there."""

    triage_item_id: str
    message_id: MessageId
    reason: ReasonCode
    claimed_case_ids: tuple[CaseId, ...]
    state: TriageState = TriageState.OPEN


def intake_decision_draft(
    message: IntakeMessage,
    decision: AuthorisationDecision,
    correlation_id: str,
) -> AuditDraft:
    """Build the ``intake.decision`` audit draft for an authorisation decision.

    Shared by ingress (``IntakeService``) and follow-up classification so both
    record the same audit shape.

    Args:
        message: The message being decided.
        decision: The authorisation decision reached.
        correlation_id: Correlation id tying related events together.

    Returns:
        An ``AuditDraft`` for the ``intake.decision`` action.
    """
    return AuditDraft(
        action=INTAKE_DECISION,
        outcome=decision.kind.value,
        subject_digest=message.body_digest,
        message_id=message.message_id,
        principal_id=message.principal_id,
        reason=decision.reason,
        detail=decision.detail,
        case_id=decision.case_id,
        correlation_id=correlation_id,
    )


# --- Slice 2: classification ------------------------------------------------


class ClassificationClass(enum.Enum):
    """Top-level route a message is classified into."""

    EXISTING_FOLLOWUP = "existing_followup"
    RESPONSE = "response"
    NEW_DELIVERABLE = "new_deliverable"


class NewDeliverableSubtype(enum.Enum):
    """The kind of new deliverable requested."""

    DUE_DILIGENCE = "due_diligence"
    SITE_SOURCING = "site_sourcing"
    TEST_FIT = "test_fit"


class MissingField(enum.Enum):
    """A required input the message did not supply."""

    SITE_GEOMETRY = "site_geometry"
    JURISDICTION = "jurisdiction"
    SEARCH_BOUNDARY = "search_boundary"
    SELECTION_CRITERIA = "selection_criteria"
    PROGRAMME = "programme"


class ClassificationOutcome(enum.Enum):
    """Final outcome of a human-confirmed classification."""

    FOLLOWUP_AUTHORISED = "followup_authorised"
    RESPONSE_READY = "response_ready"
    AWAITING_INPUTS = "awaiting_inputs"
    NEW_DELIVERABLE_READY = "new_deliverable_ready"
    TRIAGE = "triage"


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """A character span into the original message text (offsets, not content)."""

    start: int
    end: int


@dataclasses.dataclass(frozen=True, slots=True)
class MessageProjection:
    """The minimum-necessary view of a message handed to the classifier.

    ``candidate_case_ids`` are pre-scoped to cases the authenticated sender is
    authorised for, so the classifier never sees another engagement's cases.
    """

    message_id: MessageId
    principal_id: PrincipalId
    text: str
    candidate_case_ids: tuple[CaseId, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ClassificationProposal:
    """A model proposal. Advisory only — a human confirms the route.

    ``confidence`` is recorded for offline calibration and has no control-flow
    role. ``triage_reason``, when set, escalates mixed/safety-flagged mail to a
    human rather than forcing one of the three classes.
    """

    route_class: ClassificationClass
    subtype: NewDeliverableSubtype | None
    confidence: float
    evidence_spans: tuple[EvidenceSpan, ...]
    candidate_case_ids: tuple[CaseId, ...]
    missing_fields: tuple[MissingField, ...]
    triage_reason: ReasonCode | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class HumanDecision:
    """A reviewer's confirmation of the route, or an explicit triage.

    ``triage=True`` overrides everything else. For an existing follow-up the
    reviewer must set exactly one ``selected_case_id``.
    """

    reviewer_id: str
    confirmed_class: ClassificationClass | None = None
    confirmed_subtype: NewDeliverableSubtype | None = None
    selected_case_id: CaseId | None = None
    confirmed_missing_fields: tuple[MissingField, ...] = ()
    triage: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class ClassificationPayload:
    """Typed audit payload for a classification event (never a free blob)."""

    route_class: ClassificationClass | None
    subtype: NewDeliverableSubtype | None
    confidence: float | None
    missing_fields: tuple[MissingField, ...] = ()
    candidate_case_ids: tuple[CaseId, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class ClarificationTask:
    """A routine request for missing inputs on a confirmed new deliverable."""

    task_id: str
    message_id: MessageId
    subtype: NewDeliverableSubtype | None
    missing_fields: tuple[MissingField, ...]
