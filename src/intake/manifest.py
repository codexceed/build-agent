"""Versioned case manifest and the validation gate for the constraints screen.

Geometry validation is intentionally minimal and dependency-free (closed ring,
point count, CRS range, hash match). Topology checks such as self-intersection
need a geometry library and are deferred. No retrieval happens until a manifest
passes :func:`validate_manifest`.
"""

from __future__ import annotations

import dataclasses
import enum

from intake.geometry import CRS_RANGES, SUPPORTED_CRS, Ring, SiteGeometry, geometry_hash
from intake.ids import CaseId, EngagementId
from intake.model import ClassificationOutcome, NewDeliverableSubtype
from intake.protocols import CaseRegistry

SUPPORTED_JURISDICTIONS = ("US-CA",)


@dataclasses.dataclass(frozen=True, slots=True)
class CaseManifest:
    """An immutable, versioned description of the work for one case."""

    case_id: CaseId
    engagement_id: EngagementId
    jurisdiction: str
    geometry: SiteGeometry
    analysis_version: str
    revision: int


class ManifestInvalidReason(enum.Enum):
    """Why a manifest failed validation (blocks all retrieval)."""

    GEOMETRY_NOT_CLOSED = "geometry_not_closed"
    GEOMETRY_DEGENERATE = "geometry_degenerate"
    GEOMETRY_OUT_OF_RANGE = "geometry_out_of_range"
    UNSUPPORTED_CRS = "unsupported_crs"
    GEOMETRY_HASH_MISMATCH = "geometry_hash_mismatch"
    UNSUPPORTED_JURISDICTION = "unsupported_jurisdiction"
    REGISTRY_MISMATCH = "registry_mismatch"


class ManifestInvalidError(Exception):
    """Raised when a manifest is run before it passes validation."""

    def __init__(self, reason: ManifestInvalidReason) -> None:
        """Initialise with the failing reason.

        Args:
            reason: Why the manifest is invalid.
        """
        super().__init__(reason.value)
        self.reason = reason


class HandoffError(Exception):
    """Raised when a non-due-diligence or not-ready handoff is attempted."""


def _ring_area(ring: Ring) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _validate_rings(geometry: SiteGeometry) -> ManifestInvalidReason | None:
    if not geometry.rings:
        return ManifestInvalidReason.GEOMETRY_DEGENERATE
    lo_x, hi_x, lo_y, hi_y = CRS_RANGES[geometry.crs]
    for ring in geometry.rings:
        if not ring or ring[0] != ring[-1]:
            return ManifestInvalidReason.GEOMETRY_NOT_CLOSED
        if len(ring) < 4:
            return ManifestInvalidReason.GEOMETRY_DEGENERATE
        if any(not (lo_x <= x <= hi_x and lo_y <= y <= hi_y) for x, y in ring):
            return ManifestInvalidReason.GEOMETRY_OUT_OF_RANGE
        if _ring_area(ring) == 0.0:
            return ManifestInvalidReason.GEOMETRY_DEGENERATE
    return None


def validate_manifest(
    manifest: CaseManifest, registry: CaseRegistry
) -> ManifestInvalidReason | None:
    """Validate a manifest's geometry, jurisdiction, and registry ownership.

    Args:
        manifest: The manifest to validate.
        registry: The case registry used to confirm case -> engagement ownership.

    Returns:
        ``None`` if the manifest is valid, otherwise the failing reason.
    """
    geometry = manifest.geometry
    if geometry.crs not in SUPPORTED_CRS:
        return ManifestInvalidReason.UNSUPPORTED_CRS
    ring_reason = _validate_rings(geometry)
    if ring_reason is not None:
        return ring_reason
    if geometry_hash(geometry) != geometry.declared_hash:
        return ManifestInvalidReason.GEOMETRY_HASH_MISMATCH
    if manifest.jurisdiction not in SUPPORTED_JURISDICTIONS:
        return ManifestInvalidReason.UNSUPPORTED_JURISDICTION
    if registry.engagement_for_case(manifest.case_id) != manifest.engagement_id:
        return ManifestInvalidReason.REGISTRY_MISMATCH
    return None


def create_due_diligence_manifest(
    outcome: ClassificationOutcome,
    subtype: NewDeliverableSubtype | None,
    *,
    case_id: CaseId,
    engagement_id: EngagementId,
    jurisdiction: str,
    geometry: SiteGeometry,
    analysis_version: str,
) -> CaseManifest:
    """Create the first manifest revision for a confirmed due-diligence case.

    Args:
        outcome: The slice-2 confirmation outcome; must be ``NEW_DELIVERABLE_READY``.
        subtype: The confirmed deliverable subtype; must be ``DUE_DILIGENCE``.
        case_id: The case the manifest is for.
        engagement_id: The owning engagement.
        jurisdiction: The configured jurisdiction.
        geometry: The site geometry.
        analysis_version: The analysis version stamp.

    Returns:
        The first (``revision=1``) manifest revision.

    Raises:
        HandoffError: If the outcome is not ready or the subtype is not due diligence.
    """
    if outcome is not ClassificationOutcome.NEW_DELIVERABLE_READY:
        raise HandoffError("handoff requires a ready new deliverable")
    if subtype is not NewDeliverableSubtype.DUE_DILIGENCE:
        raise HandoffError("handoff requires due diligence")
    return CaseManifest(
        case_id=case_id,
        engagement_id=engagement_id,
        jurisdiction=jurisdiction,
        geometry=geometry,
        analysis_version=analysis_version,
        revision=1,
    )
