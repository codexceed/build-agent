"""Reusable authorisation gate shared by ingress and classification.

Wraps the registry snapshot and the pure :func:`evaluate_authorisation` so both
``IntakeService`` (at ingress) and ``ClassificationService`` (at follow-up
confirmation) evaluate the exact same chain with no duplicated policy.
"""

from __future__ import annotations

import datetime as dt

from intake.authorisation import evaluate_authorisation
from intake.ids import CaseId, PrincipalId
from intake.model import AuthorisationDecision
from intake.protocols import CaseRegistry


def authorise(
    registry: CaseRegistry,
    principal_id: PrincipalId,
    case_id: CaseId,
    as_of: dt.datetime,
) -> AuthorisationDecision:
    """Resolve and evaluate the authorisation chain for one case.

    Args:
        registry: The case registry to resolve relationships from.
        principal_id: The authenticated principal.
        case_id: The case the principal wants to attach to.
        as_of: The instant at which authorisation validity is judged.

    Returns:
        The authorisation decision; an authorised result echoes the case id and
        a denial carries no case metadata.
    """
    snapshot = registry.authorisation_snapshot(principal_id, case_id, as_of)
    return evaluate_authorisation(snapshot)
