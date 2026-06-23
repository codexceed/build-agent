"""Minimal, dependency-free site-geometry value type and content hashing.

A leaf module (no intra-package imports) so both the manifest and the evidence
types can depend on it without a cycle.
"""

from __future__ import annotations

import dataclasses
import hashlib

SUPPORTED_CRS = ("EPSG:4326",)
CRS_RANGES = {"EPSG:4326": (-180.0, 180.0, -90.0, 90.0)}

Ring = tuple[tuple[float, float], ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SiteGeometry:
    """A site polygon with its CRS, declared content hash, and accuracy."""

    crs: str
    rings: tuple[Ring, ...]
    accuracy: str  # indicative | parcel | survey
    declared_hash: str


def geometry_hash(geometry: SiteGeometry) -> str:
    """Compute the canonical content hash of a geometry (excludes the declared hash).

    Args:
        geometry: The geometry to hash.

    Returns:
        A ``sha256:``-prefixed hex digest of the CRS and ring coordinates.
    """
    canonical = (
        geometry.crs
        + "|"
        + ";".join(
            ",".join(f"{x:.6f}:{y:.6f}" for x, y in ring) for ring in geometry.rings
        )
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
