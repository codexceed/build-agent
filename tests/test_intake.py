"""Slice-1 breaking tests: authorisation gate, audit trail, triage queue.

Behaviour is pinned before implementation (TDD). Dropped for this slice and
left as TODOs for the durable-storage stage: hash-chain tamper verification,
caller-held-reference immutability, and write atomicity.
"""

from __future__ import annotations

import datetime as dt

from intake.ids import CaseId, EngagementId, MessageId, PrincipalId
from intake.model import DecisionKind, IntakeMessage, ReasonCode

from .conftest import BASE, World


def _message(
    principal: str,
    case_ids: tuple[str, ...],
    message_id: str = "M1",
) -> IntakeMessage:
    return IntakeMessage(
        message_id=MessageId(message_id),
        principal_id=PrincipalId(principal),
        claimed_case_ids=tuple(CaseId(c) for c in case_ids),
        body_digest="sha256:body",
        attachment_count=0,
    )


# --- Authorisation (pure core via the service) -----------------------------


def test_valid_chain_is_authorised(world: World) -> None:
    decision = world.service.handle_intake(_message("P", ("CASE-1",)))
    assert decision.kind is DecisionKind.AUTHORISED
    assert decision.case_id == CaseId("CASE-1")


def test_different_client_is_triaged_without_metadata(world: World) -> None:
    decision = world.service.handle_intake(_message("P2", ("CASE-1",)))
    assert decision.kind is DecisionKind.TRIAGE
    assert decision.case_id is None


def test_mixed_chains_cannot_authorise(world: World) -> None:
    # Principal P (client C) against CASE-2 (engagement E2, client C2).
    decision = world.service.handle_intake(_message("P", ("CASE-2",)))
    assert decision.kind is DecisionKind.TRIAGE
    assert decision.case_id is None


def test_unknown_or_malformed_case_id_is_triaged(world: World) -> None:
    unknown = world.service.handle_intake(_message("P", ("CASE-999",), message_id="M1"))
    malformed = world.service.handle_intake(_message("P", ("not a case id",), message_id="M2"))
    assert unknown.kind is DecisionKind.TRIAGE
    assert malformed.kind is DecisionKind.TRIAGE
    assert unknown.case_id is None and malformed.case_id is None


def test_multiple_claimed_case_ids_are_ambiguous(world: World) -> None:
    decision = world.service.handle_intake(_message("P", ("CASE-1", "CASE-2")))
    assert decision.kind is DecisionKind.TRIAGE
    assert decision.reason is ReasonCode.AMBIGUOUS_CASE_REFERENCE


def test_revoked_principal_is_triaged_as_of_request_time(world: World) -> None:
    world.clock.set(BASE + dt.timedelta(seconds=1))
    decision = world.service.handle_intake(_message("P-revoked", ("CASE-1",)))
    assert decision.kind is DecisionKind.TRIAGE
    assert decision.case_id is None


def test_denial_exposes_reason_but_no_case_metadata(world: World) -> None:
    decision = world.service.handle_intake(_message("P2", ("CASE-1",)))
    assert decision.reason is not None
    assert decision.case_id is None
    # No client/engagement attributes leak onto the decision.
    assert not hasattr(decision, "client_id")
    assert not hasattr(decision, "engagement_id")


# --- Identity (trusted principal field) ------------------------------------


def test_authorisation_depends_only_on_principal_id(world: World) -> None:
    # An unauthenticated/unknown principal cannot be authorised even with a
    # valid case id; identity is the trusted principal, never a display name.
    decision = world.service.handle_intake(_message("P-spoofed", ("CASE-1",)))
    assert decision.kind is DecisionKind.TRIAGE
    # IntakeMessage carries no display-name/address field to parse.
    assert "sender_name" not in IntakeMessage.__dataclass_fields__
    assert "from_address" not in IntakeMessage.__dataclass_fields__


# --- Audit (append-only, ordered) ------------------------------------------


def test_each_decision_writes_one_ordered_audit_event(world: World) -> None:
    before = len(world.audit.events)
    world.service.handle_intake(_message("P", ("CASE-1",)))
    after = world.audit.events
    assert len(after) == before + 1
    sequences = [event.sequence for event in after]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_register_case_writes_one_audit_event(world: World) -> None:
    before = len(world.audit.events)
    world.service.register_case(EngagementId("E"), CaseId("CASE-3"))
    assert len(world.audit.events) == before + 1


def test_audit_payload_stores_digests_and_ids_only(world: World) -> None:
    message = _message("P", ("CASE-1",))
    world.service.handle_intake(message)
    event = world.audit.decision_for(message.message_id)
    assert event is not None
    # Only a content digest and opaque ids are stored — never the raw body.
    assert event.subject_digest == message.body_digest
    assert event.message_id == message.message_id
    assert not hasattr(event, "body")


# --- Triage queue ----------------------------------------------------------


def test_triage_enqueues_one_item_with_reason(world: World) -> None:
    world.service.handle_intake(_message("P2", ("CASE-1",)))
    assert len(world.triage.items) == 1
    assert world.triage.items[0].reason is ReasonCode.NOT_AUTHORISED


def test_unlinked_message_is_triaged(world: World) -> None:
    decision = world.service.handle_intake(_message("P", ()))
    assert decision.kind is DecisionKind.TRIAGE
    assert decision.reason is ReasonCode.NO_CASE_REFERENCE


# --- Idempotency -----------------------------------------------------------


def test_retried_intake_is_idempotent(world: World) -> None:
    message = _message("P2", ("CASE-1",))
    first = world.service.handle_intake(message)
    audit_after_first = len(world.audit.events)
    triage_after_first = len(world.triage.items)

    second = world.service.handle_intake(message)
    assert second.kind is first.kind
    assert second.reason is first.reason
    assert len(world.audit.events) == audit_after_first
    assert len(world.triage.items) == triage_after_first
