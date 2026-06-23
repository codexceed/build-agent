"""Slice 3: run the validated manifest through the three sources, fail-closed.

The service validates the manifest, issues exactly one query per source in fixed
order, and normalises every outcome fail-closed: any exception, timeout, source
mismatch, or incomplete coverage becomes ``UNKNOWN`` — never a green result.
Findings are *derived* from raw evidence records, never fabricated, and every
finding cites the record it rests on. No caching/backpressure (ADR-0002).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from collections.abc import Mapping

from intake.evidence_model import (
    AdapterResult,
    Coverage,
    EvidenceQuery,
    EvidenceRecord,
    EvidenceStatus,
    Finding,
    FindingsLedger,
    SourceId,
)
from intake.geometry import geometry_hash
from intake.manifest import CaseManifest, ManifestInvalidError, validate_manifest
from intake.protocols import CaseRegistry, Clock, EvidenceStore, IdGenerator, SourceAdapter

_logger = logging.getLogger(__name__)
_DEFAULT_BUFFER_M = 100.0


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _failure_envelope(source_id: SourceId, exc: BaseException) -> str:
    return f"FAILURE|{source_id.value}|{type(exc).__name__}"


def _normalise(query: EvidenceQuery, result: AdapterResult) -> tuple[EvidenceStatus, str | None]:
    """Apply the fail-closed rules to an adapter result.

    Args:
        query: The query that was issued.
        result: The adapter's raw result.

    Returns:
        The normalised status and a non-sensitive failure code (or ``None``).
    """
    if result.source_id is not query.source_id:
        return EvidenceStatus.UNKNOWN, "source_mismatch"
    if result.coverage is not Coverage.COMPLETE:
        return EvidenceStatus.UNKNOWN, "incomplete_coverage"
    return result.status, None


class EvidenceService:  # pylint: disable=too-few-public-methods
    """Builds the desktop constraints screen for a validated case manifest."""

    def __init__(
        self,
        registry: CaseRegistry,
        store: EvidenceStore,
        adapters: Mapping[SourceId, SourceAdapter],
        clock: Clock,
        ids: IdGenerator,
        buffer_m: float = _DEFAULT_BUFFER_M,
    ) -> None:
        if set(adapters) != set(SourceId):
            raise ValueError("exactly one adapter per source is required")
        for source_id, adapter in adapters.items():
            if adapter.source_id is not source_id:
                raise ValueError(f"adapter for {source_id} reports {adapter.source_id}")
        self._registry = registry
        self._store = store
        self._adapters = dict(adapters)
        self._clock = clock
        self._ids = ids
        self._buffer_m = buffer_m

    def run(self, manifest: CaseManifest) -> FindingsLedger:
        """Validate the manifest, query all sources, and assemble the ledger.

        Args:
            manifest: The case manifest to screen.

        Returns:
            The persisted findings ledger for this manifest revision.

        Raises:
            ManifestInvalidError: If the manifest fails validation; no source is
                queried in that case.
        """
        reason = validate_manifest(manifest, self._registry)
        if reason is not None:
            raise ManifestInvalidError(reason)

        records = tuple(
            self._attempt(manifest, self._build_query(manifest, source_id))
            for source_id in SourceId
        )
        findings = tuple(self._derive_finding(record) for record in records)
        ledger = FindingsLedger(
            ledger_id=self._ids.new_id(),
            case_id=manifest.case_id,
            analysis_version=manifest.analysis_version,
            manifest_revision=manifest.revision,
            records=records,
            findings=findings,
        )
        _logger.info(
            "evidence_ledger",
            extra={
                "case_id": manifest.case_id,
                "manifest_revision": manifest.revision,
                "statuses": {r.source_id.value: r.status.value for r in records},
            },
        )
        return self._store.save_ledger(ledger)

    def _build_query(self, manifest: CaseManifest, source_id: SourceId) -> EvidenceQuery:
        query_geometry_hash = _sha(f"{geometry_hash(manifest.geometry)}|buffer={self._buffer_m}")
        return EvidenceQuery(
            source_id=source_id,
            case_id=manifest.case_id,
            jurisdiction=manifest.jurisdiction,
            geometry=manifest.geometry,
            buffer_m=self._buffer_m,
            query_geometry_hash=query_geometry_hash,
            crs=manifest.geometry.crs,
        )

    def _attempt(self, manifest: CaseManifest, query: EvidenceQuery) -> EvidenceRecord:
        retrieved_at = self._clock.now()
        try:
            result = self._adapters[query.source_id].fetch(query)
            status, failure_code = _normalise(query, result)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            return self._failure_record(manifest, query, exc, retrieved_at)
        return EvidenceRecord(
            record_id=self._ids.new_id(),
            case_id=manifest.case_id,
            analysis_version=manifest.analysis_version,
            manifest_revision=manifest.revision,
            source_id=query.source_id,
            status=status,
            coverage=result.coverage,
            request=result.request,
            raw_hash=_sha(result.raw),
            query_geometry_hash=query.query_geometry_hash,
            parser_version=result.parser_version,
            crs=result.crs,
            retrieved_at=retrieved_at,
            as_of=result.as_of,
            failure_code=failure_code,
        )

    def _failure_record(
        self,
        manifest: CaseManifest,
        query: EvidenceQuery,
        exc: BaseException,
        retrieved_at: dt.datetime,
    ) -> EvidenceRecord:
        return EvidenceRecord(
            record_id=self._ids.new_id(),
            case_id=manifest.case_id,
            analysis_version=manifest.analysis_version,
            manifest_revision=manifest.revision,
            source_id=query.source_id,
            status=EvidenceStatus.UNKNOWN,
            coverage=Coverage.MISSING,
            request=f"{query.source_id.value}:{query.query_geometry_hash}",
            raw_hash=_sha(_failure_envelope(query.source_id, exc)),
            query_geometry_hash=query.query_geometry_hash,
            parser_version="n/a",
            crs=query.crs,
            retrieved_at=retrieved_at,
            as_of=None,
            failure_code=type(exc).__name__,
        )

    def _derive_finding(self, record: EvidenceRecord) -> Finding:
        is_unknown = record.status is EvidenceStatus.UNKNOWN
        summary = (
            "evidence gap"
            if is_unknown
            else f"{record.source_id.value}:{record.status.value}"
        )
        return Finding(
            finding_id=self._ids.new_id(),
            source_id=record.source_id,
            status=record.status,
            evidence_record_ids=(record.record_id,),
            summary=summary,
            is_exclusion=is_unknown,
        )
