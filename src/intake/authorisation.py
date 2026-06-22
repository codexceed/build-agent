"""Pure authorisation policy: turn a resolved snapshot into a decision.

This module has no I/O. It is the security core of slice 1 and is exhaustively
covered by breaking tests before anything calls it.
"""

from intake.model import (
    AuthorisationDecision,
    AuthorisationSnapshot,
    DecisionKind,
    DenialDetail,
    ReasonCode,
)


def _deny(detail: DenialDetail) -> AuthorisationDecision:
    return AuthorisationDecision(
        kind=DecisionKind.TRIAGE,
        reason=ReasonCode.NOT_AUTHORISED,
        detail=detail,
    )


def evaluate_authorisation(snapshot: AuthorisationSnapshot) -> AuthorisationDecision:
    """Decide whether an attach request is authorised.

    Authorises only when the full ``sender -> client -> engagement -> case``
    chain is intact and the principal's authorisation is active as of the
    snapshot's evaluation time. Any failure yields a triage decision that
    carries no case metadata; the specific cause is recorded only as an
    audit-only ``detail``.

    Args:
        snapshot: Resolved relationship facts for the request.

    Returns:
        An authorised decision echoing the validated case id, or a triage
        decision with a client-safe reason and an audit-only detail.
    """
    if not snapshot.case_present or snapshot.case_id is None:
        return _deny(DenialDetail.CASE_NOT_FOUND)
    if snapshot.principal_client is None:
        return _deny(DenialDetail.PRINCIPAL_UNKNOWN)
    if not snapshot.principal_active:
        return _deny(DenialDetail.AUTHORISATION_REVOKED)
    if (
        snapshot.engagement_client is None
        or snapshot.principal_client != snapshot.engagement_client
    ):
        return _deny(DenialDetail.CLIENT_MISMATCH)
    return AuthorisationDecision(kind=DecisionKind.AUTHORISED, case_id=snapshot.case_id)
