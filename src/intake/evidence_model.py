"""Evidence types: raw provenance records versus derived findings.

An ``EvidenceRecord`` is immutable raw provenance for one source attempt and
carries no conclusion. A ``Finding`` is *derived* and must cite the evidence it
rests on. ``UNKNOWN`` is never a green result — it is an explicit evidence gap.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from intake.geometry import SiteGeometry
from intake.ids import CaseId


class SourceId(enum.Enum):
    """The three configured evidence sources, in fixed execution order."""

    PLANNING_ZONING = "planning_zoning"
    ENVIRONMENTAL_REGISTRY = "environmental_registry"
    UTILITY_RTO = "utility_rto"


class EvidenceStatus(enum.Enum):
    """The fail-closed status of one source query."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


class Coverage(enum.Enum):
    """Whether a source answered for the whole query area."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceQuery:
    """A fully validated query handed to one source adapter."""

    source_id: SourceId
    case_id: CaseId
    jurisdiction: str
    geometry: SiteGeometry
    buffer_m: float
    query_geometry_hash: str
    crs: str


@dataclasses.dataclass(frozen=True, slots=True)
class AdapterResult:
    """What a source adapter returns; the service normalises it fail-closed."""

    source_id: SourceId
    status: EvidenceStatus
    coverage: Coverage
    request: str
    raw: str
    parser_version: str
    crs: str
    as_of: dt.date | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Immutable raw provenance for one source attempt (no conclusion)."""

    record_id: str
    case_id: CaseId
    analysis_version: str
    manifest_revision: int
    source_id: SourceId
    status: EvidenceStatus
    coverage: Coverage
    request: str
    raw_hash: str
    query_geometry_hash: str
    parser_version: str
    crs: str
    retrieved_at: dt.datetime
    as_of: dt.date | None
    failure_code: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """A derived finding that must cite the evidence record(s) it rests on."""

    finding_id: str
    source_id: SourceId
    status: EvidenceStatus
    evidence_record_ids: tuple[str, ...]
    summary: str
    is_exclusion: bool


@dataclasses.dataclass(frozen=True, slots=True)
class FindingsLedger:
    """An immutable ledger tying findings to raw evidence for one manifest revision."""

    ledger_id: str
    case_id: CaseId
    analysis_version: str
    manifest_revision: int
    records: tuple[EvidenceRecord, ...]
    findings: tuple[Finding, ...]
