"""Slice-2 breaking tests: classify, propose, confirm.

The classifier proposes; a human confirms every route. Confidence never
auto-routes; an existing follow-up is re-authorised before attach.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from intake.classification import ConflictingDecisionError
from intake.ids import CaseId, MessageId, PrincipalId
from intake.model import (
    CLASSIFICATION_CONFIRMED,
    CLASSIFICATION_CORRECTED,
    CLASSIFICATION_PROPOSED,
    FOLLOWUP_AUTH_CHECKED,
    ClassificationClass,
    ClassificationOutcome,
    ClassificationProposal,
    EvidenceSpan,
    HumanDecision,
    IntakeMessage,
    MissingField,
    NewDeliverableSubtype,
    ReasonCode,
)

from .conftest import CWorld

MakeCWorld = Callable[..., CWorld]


def _msg(principal: str, case_ids: Sequence[str] = (), message_id: str = "M1") -> IntakeMessage:
    return IntakeMessage(
        message_id=MessageId(message_id),
        principal_id=PrincipalId(principal),
        claimed_case_ids=tuple(CaseId(c) for c in case_ids),
        body_digest="sha256:body",
        attachment_count=0,
    )


def _followup(
    candidates: Sequence[str] = ("CASE-1",),
    confidence: float = 0.9,
    triage_reason: ReasonCode | None = None,
) -> ClassificationProposal:
    return ClassificationProposal(
        route_class=ClassificationClass.EXISTING_FOLLOWUP,
        subtype=None,
        confidence=confidence,
        evidence_spans=(EvidenceSpan(0, 4),),
        candidate_case_ids=tuple(CaseId(c) for c in candidates),
        missing_fields=(),
        triage_reason=triage_reason,
    )


def _response(
    confidence: float = 0.8, triage_reason: ReasonCode | None = None
) -> ClassificationProposal:
    return ClassificationProposal(
        route_class=ClassificationClass.RESPONSE,
        subtype=None,
        confidence=confidence,
        evidence_spans=(),
        candidate_case_ids=(),
        missing_fields=(),
        triage_reason=triage_reason,
    )


def _new(
    subtype: NewDeliverableSubtype, missing: Sequence[MissingField] = ()
) -> ClassificationProposal:
    return ClassificationProposal(
        route_class=ClassificationClass.NEW_DELIVERABLE,
        subtype=subtype,
        confidence=0.8,
        evidence_spans=(),
        candidate_case_ids=(),
        missing_fields=tuple(missing),
        triage_reason=None,
    )


# --- Proposal --------------------------------------------------------------


def test_proposal_is_recorded(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_followup())
    result = cw.service.propose(_msg("P", ("CASE-1",)), text="clarify page 7")
    assert result.proposal is not None
    event = cw.audit.event_for(MessageId("M1"), CLASSIFICATION_PROPOSED)
    assert event is not None and event.classification is not None
    assert event.classification.route_class is ClassificationClass.EXISTING_FOLLOWUP
    assert event.classification.confidence == 0.9


def test_proposing_is_idempotent(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())
    message = _msg("P", ())
    cw.service.propose(message, text="hello")
    cw.service.propose(message, text="hello")
    assert cw.classifier.calls == 1
    proposed = [e for e in cw.audit.events if e.action == CLASSIFICATION_PROPOSED]
    assert len(proposed) == 1


def test_classifier_failure_is_triage(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(error=True)
    result = cw.service.propose(_msg("P", ()), text="...")
    assert result.triaged
    assert [item.reason for item in cw.triage.items] == [ReasonCode.CLASSIFIER_ERROR]


def test_high_confidence_does_not_auto_route(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response(confidence=1.0))
    result = cw.service.propose(_msg("P", ()), text="...")
    assert not result.triaged and result.proposal is not None
    assert cw.audit.event_for(MessageId("M1"), CLASSIFICATION_CONFIRMED) is None
    assert cw.triage.items == () and cw.clarifications.items == ()


# --- Confirm / correct -----------------------------------------------------


def test_human_confirms_route(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message, HumanDecision(reviewer_id="R1", confirmed_class=ClassificationClass.RESPONSE)
    )
    assert result.outcome is ClassificationOutcome.RESPONSE_READY
    confirmed = cw.audit.event_for(MessageId("M1"), CLASSIFICATION_CONFIRMED)
    assert confirmed is not None and confirmed.reviewer_id == "R1"


def test_human_override_records_correction(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())  # model said RESPONSE
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message,
        HumanDecision(
            reviewer_id="R1",
            confirmed_class=ClassificationClass.NEW_DELIVERABLE,
            confirmed_subtype=NewDeliverableSubtype.DUE_DILIGENCE,
        ),
    )
    assert result.outcome is ClassificationOutcome.NEW_DELIVERABLE_READY
    assert cw.audit.event_for(MessageId("M1"), CLASSIFICATION_CORRECTED) is not None


def test_confirmation_is_idempotent(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    decision = HumanDecision(reviewer_id="R1", confirmed_class=ClassificationClass.RESPONSE)
    first = cw.service.confirm(message, decision)
    events_after_first = len(cw.audit.events)
    second = cw.service.confirm(message, decision)
    assert second.outcome is first.outcome
    assert len(cw.audit.events) == events_after_first


def test_conflicting_second_decision_is_rejected(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    cw.service.confirm(
        message, HumanDecision(reviewer_id="R1", confirmed_class=ClassificationClass.RESPONSE)
    )
    with pytest.raises(ConflictingDecisionError):
        cw.service.confirm(
            message,
            HumanDecision(
                reviewer_id="R1",
                confirmed_class=ClassificationClass.NEW_DELIVERABLE,
                confirmed_subtype=NewDeliverableSubtype.DUE_DILIGENCE,
            ),
        )


# --- Existing-followup authorisation (composition) -------------------------


def test_confirmed_followup_authorises(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_followup())
    message = _msg("P", ("CASE-1",))
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message,
        HumanDecision(
            reviewer_id="R1",
            confirmed_class=ClassificationClass.EXISTING_FOLLOWUP,
            selected_case_id=CaseId("CASE-1"),
        ),
    )
    assert result.outcome is ClassificationOutcome.FOLLOWUP_AUTHORISED
    assert result.case_id == CaseId("CASE-1")
    assert cw.audit.event_for(MessageId("M1"), FOLLOWUP_AUTH_CHECKED) is not None


def test_confirmed_followup_denied_without_metadata(make_cworld: MakeCWorld) -> None:
    # Reviewer selects a cross-client candidate the model surfaced; the final
    # gate must still deny and disclose nothing.
    cw = make_cworld(proposal=_followup(candidates=("CASE-2",)))
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message,
        HumanDecision(
            reviewer_id="R1",
            confirmed_class=ClassificationClass.EXISTING_FOLLOWUP,
            selected_case_id=CaseId("CASE-2"),
        ),
    )
    assert result.outcome is ClassificationOutcome.TRIAGE
    assert result.case_id is None
    assert len(cw.triage.items) == 1


def test_followup_requires_one_selected_case(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_followup())
    message = _msg("P", ("CASE-1",))
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message,
        HumanDecision(
            reviewer_id="R1",
            confirmed_class=ClassificationClass.EXISTING_FOLLOWUP,
            selected_case_id=None,
        ),
    )
    assert result.outcome is ClassificationOutcome.TRIAGE
    assert cw.triage.items[0].reason is ReasonCode.NO_CASE_SELECTED


def test_unauthorised_claimed_id_never_reaches_classifier(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_followup())
    result = cw.service.propose(_msg("P2", ("CASE-1",)), text="...")
    assert result.triaged
    assert cw.classifier.calls == 0
    assert len(cw.triage.items) == 1


# --- New deliverable / response --------------------------------------------


def test_confirmed_new_deliverable_ready(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_new(NewDeliverableSubtype.DUE_DILIGENCE))
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message,
        HumanDecision(
            reviewer_id="R1",
            confirmed_class=ClassificationClass.NEW_DELIVERABLE,
            confirmed_subtype=NewDeliverableSubtype.DUE_DILIGENCE,
        ),
    )
    assert result.outcome is ClassificationOutcome.NEW_DELIVERABLE_READY
    assert cw.clarifications.items == ()


def test_missing_fields_awaits_inputs(make_cworld: MakeCWorld) -> None:
    missing = (MissingField.SITE_GEOMETRY, MissingField.JURISDICTION)
    cw = make_cworld(proposal=_new(NewDeliverableSubtype.DUE_DILIGENCE, missing=missing))
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message,
        HumanDecision(
            reviewer_id="R1",
            confirmed_class=ClassificationClass.NEW_DELIVERABLE,
            confirmed_subtype=NewDeliverableSubtype.DUE_DILIGENCE,
            confirmed_missing_fields=missing,
        ),
    )
    assert result.outcome is ClassificationOutcome.AWAITING_INPUTS
    assert len(cw.clarifications.items) == 1
    assert cw.clarifications.items[0].missing_fields == missing
    assert cw.triage.items == ()  # routine clarification, not security triage


def test_confirmed_response_ready(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    result = cw.service.confirm(
        message, HumanDecision(reviewer_id="R1", confirmed_class=ClassificationClass.RESPONSE)
    )
    assert result.outcome is ClassificationOutcome.RESPONSE_READY
    assert cw.triage.items == () and cw.clarifications.items == ()


def test_flagged_proposal_is_triage(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response(triage_reason=ReasonCode.MIXED_OR_FLAGGED))
    result = cw.service.propose(_msg("P", ()), text="...")
    assert result.triaged
    assert cw.triage.items[0].reason is ReasonCode.MIXED_OR_FLAGGED


# --- Audit -----------------------------------------------------------------


def test_propose_and_confirm_share_correlation_id(make_cworld: MakeCWorld) -> None:
    cw = make_cworld(proposal=_response())
    message = _msg("P", ())
    cw.service.propose(message, text="...")
    cw.service.confirm(
        message, HumanDecision(reviewer_id="R1", confirmed_class=ClassificationClass.RESPONSE)
    )
    proposed = cw.audit.event_for(MessageId("M1"), CLASSIFICATION_PROPOSED)
    confirmed = cw.audit.event_for(MessageId("M1"), CLASSIFICATION_CONFIRMED)
    assert proposed is not None and confirmed is not None
    assert proposed.correlation_id == confirmed.correlation_id
    assert proposed.sequence < confirmed.sequence
