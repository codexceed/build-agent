"""Slice-3 breaking tests: validated site evidence retrieval.

The manifest gate blocks all retrieval until geometry, jurisdiction, and
registry ownership validate. Adapters fail closed to UNKNOWN; findings are
derived from raw evidence and always cite it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pytest

from intake.evidence import EvidenceService
from intake.evidence_model import (
    AdapterResult,
    Coverage,
    EvidenceStatus,
    Finding,
    FindingsLedger,
    SourceId,
)
from intake.geometry import Ring, SiteGeometry, geometry_hash
from intake.ids import CaseId, EngagementId
from intake.manifest import (
    CaseManifest,
    HandoffError,
    ManifestInvalidError,
    ManifestInvalidReason,
    create_due_diligence_manifest,
)
from intake.memory import InMemoryCaseRegistry, InMemoryEvidenceStore
from intake.model import ClassificationOutcome, NewDeliverableSubtype

from .conftest import BASE, EWorld, FakeAdapter, FixedClock, SequentialIdGenerator

MakeEWorld = Callable[..., EWorld]

_SQUARE: Ring = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))


def _geometry(crs: str = "EPSG:4326", rings: tuple[Ring, ...] = (_SQUARE,)) -> SiteGeometry:
    geom = SiteGeometry(crs=crs, rings=rings, accuracy="indicative", declared_hash="")
    return dataclasses.replace(geom, declared_hash=geometry_hash(geom))


def _manifest(
    geometry: SiteGeometry | None = None,
    *,
    case_id: str = "CASE-1",
    engagement_id: str = "E",
    jurisdiction: str = "US-CA",
    revision: int = 1,
) -> CaseManifest:
    return CaseManifest(
        case_id=CaseId(case_id),
        engagement_id=EngagementId(engagement_id),
        jurisdiction=jurisdiction,
        geometry=geometry if geometry is not None else _geometry(),
        analysis_version="screen@0.1",
        revision=revision,
    )


def _result(
    source_id: SourceId, status: EvidenceStatus, coverage: Coverage = Coverage.COMPLETE
) -> AdapterResult:
    return AdapterResult(
        source_id=source_id,
        status=status,
        coverage=coverage,
        request=f"req:{source_id.value}",
        raw="raw-payload",
        parser_version="v1",
        crs="EPSG:4326",
    )


def _assert_no_calls(world: EWorld) -> None:
    assert all(adapter.calls == 0 for adapter in world.adapters.values())


# --- Manifest & validation gate --------------------------------------------


def test_valid_manifest_runs(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    ledger = world.service.run(_manifest())
    assert isinstance(ledger, FindingsLedger)
    assert len(ledger.records) == 3


def test_non_closed_polygon_blocks_all(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    open_ring: Ring = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(_geometry(rings=(open_ring,))))
    assert exc.value.reason is ManifestInvalidReason.GEOMETRY_NOT_CLOSED
    _assert_no_calls(world)


def test_degenerate_polygon_blocks_all(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    degenerate: Ring = ((0.0, 0.0), (0.0, 0.0), (0.0, 0.0))
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(_geometry(rings=(degenerate,))))
    assert exc.value.reason is ManifestInvalidReason.GEOMETRY_DEGENERATE
    _assert_no_calls(world)


def test_out_of_range_coords_block_all(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    bad: Ring = ((0.0, 0.0), (0.0, 200.0), (1.0, 200.0), (1.0, 0.0), (0.0, 0.0))
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(_geometry(rings=(bad,))))
    assert exc.value.reason is ManifestInvalidReason.GEOMETRY_OUT_OF_RANGE
    _assert_no_calls(world)


def test_unsupported_crs_blocks_all(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(_geometry(crs="EPSG:9999")))
    assert exc.value.reason is ManifestInvalidReason.UNSUPPORTED_CRS
    _assert_no_calls(world)


def test_geometry_hash_mismatch_blocks_all(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    tampered = dataclasses.replace(_geometry(), declared_hash="sha256:wrong")
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(tampered))
    assert exc.value.reason is ManifestInvalidReason.GEOMETRY_HASH_MISMATCH
    _assert_no_calls(world)


def test_unsupported_jurisdiction_blocks_all(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(jurisdiction="US-ZZ"))
    assert exc.value.reason is ManifestInvalidReason.UNSUPPORTED_JURISDICTION
    _assert_no_calls(world)


def test_registry_mismatch_is_rejected(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    # CASE-1 is owned by engagement E, not E-OTHER.
    with pytest.raises(ManifestInvalidError) as exc:
        world.service.run(_manifest(engagement_id="E-OTHER"))
    assert exc.value.reason is ManifestInvalidReason.REGISTRY_MISMATCH
    _assert_no_calls(world)


def _handoff(
    outcome: ClassificationOutcome, subtype: NewDeliverableSubtype
) -> CaseManifest:
    return create_due_diligence_manifest(
        outcome,
        subtype,
        case_id=CaseId("CASE-1"),
        engagement_id=EngagementId("E"),
        jurisdiction="US-CA",
        geometry=_geometry(),
        analysis_version="screen@0.1",
    )


def test_handoff_rejects_non_due_diligence_or_not_ready() -> None:
    with pytest.raises(HandoffError):
        _handoff(ClassificationOutcome.AWAITING_INPUTS, NewDeliverableSubtype.DUE_DILIGENCE)
    with pytest.raises(HandoffError):
        _handoff(ClassificationOutcome.NEW_DELIVERABLE_READY, NewDeliverableSubtype.SITE_SOURCING)
    ok = _handoff(ClassificationOutcome.NEW_DELIVERABLE_READY, NewDeliverableSubtype.DUE_DILIGENCE)
    assert ok.revision == 1


# --- Adapter execution & fail-closed ---------------------------------------


def test_construction_rejects_wrong_adapter_set() -> None:
    registry = InMemoryCaseRegistry()
    store = InMemoryEvidenceStore()
    clock = FixedClock(BASE)
    ids = SequentialIdGenerator()
    partial = {
        SourceId.PLANNING_ZONING: FakeAdapter(SourceId.PLANNING_ZONING),
        SourceId.ENVIRONMENTAL_REGISTRY: FakeAdapter(SourceId.ENVIRONMENTAL_REGISTRY),
    }
    with pytest.raises(ValueError, match="one adapter per source"):
        EvidenceService(registry, store, partial, clock, ids)
    mismatched = {
        SourceId.PLANNING_ZONING: FakeAdapter(SourceId.UTILITY_RTO),
        SourceId.ENVIRONMENTAL_REGISTRY: FakeAdapter(SourceId.ENVIRONMENTAL_REGISTRY),
        SourceId.UTILITY_RTO: FakeAdapter(SourceId.UTILITY_RTO),
    }
    with pytest.raises(ValueError, match="reports"):
        EvidenceService(registry, store, mismatched, clock, ids)


def test_runs_three_queries_in_fixed_order(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    manifest = _manifest()
    ledger = world.service.run(manifest)
    assert [r.source_id for r in ledger.records] == [
        SourceId.PLANNING_ZONING,
        SourceId.ENVIRONMENTAL_REGISTRY,
        SourceId.UTILITY_RTO,
    ]
    for adapter in world.adapters.values():
        assert adapter.calls == 1
        assert adapter.query is not None
        assert adapter.query.geometry == manifest.geometry
        assert adapter.query.buffer_m == 100.0


def test_adapter_exception_becomes_unknown(make_eworld: MakeEWorld) -> None:
    planning = FakeAdapter(SourceId.PLANNING_ZONING, error=RuntimeError("boom"))
    world = make_eworld(planning=planning)
    ledger = world.service.run(_manifest())
    record = next(r for r in ledger.records if r.source_id is SourceId.PLANNING_ZONING)
    assert record.status is EvidenceStatus.UNKNOWN
    assert record.failure_code == "RuntimeError"


def test_partial_coverage_forced_to_unknown(make_eworld: MakeEWorld) -> None:
    planning = FakeAdapter(
        SourceId.PLANNING_ZONING,
        result=_result(SourceId.PLANNING_ZONING, EvidenceStatus.FOUND, Coverage.PARTIAL),
    )
    world = make_eworld(planning=planning)
    ledger = world.service.run(_manifest())
    record = next(r for r in ledger.records if r.source_id is SourceId.PLANNING_ZONING)
    assert record.status is EvidenceStatus.UNKNOWN


def test_complete_found_and_not_found_preserved(make_eworld: MakeEWorld) -> None:
    planning = FakeAdapter(
        SourceId.PLANNING_ZONING, result=_result(SourceId.PLANNING_ZONING, EvidenceStatus.FOUND)
    )
    utility = FakeAdapter(
        SourceId.UTILITY_RTO, result=_result(SourceId.UTILITY_RTO, EvidenceStatus.NOT_FOUND)
    )
    world = make_eworld(planning=planning, utility=utility)
    ledger = world.service.run(_manifest())
    by_source = {r.source_id: r.status for r in ledger.records}
    assert by_source[SourceId.PLANNING_ZONING] is EvidenceStatus.FOUND
    assert by_source[SourceId.UTILITY_RTO] is EvidenceStatus.NOT_FOUND


def test_one_adapter_failure_does_not_sink_others(make_eworld: MakeEWorld) -> None:
    planning = FakeAdapter(SourceId.PLANNING_ZONING, error=TimeoutError("slow"))
    environmental = FakeAdapter(
        SourceId.ENVIRONMENTAL_REGISTRY,
        result=_result(SourceId.ENVIRONMENTAL_REGISTRY, EvidenceStatus.FOUND),
    )
    world = make_eworld(planning=planning, environmental=environmental)
    ledger = world.service.run(_manifest())
    by_source = {r.source_id: r.status for r in ledger.records}
    assert by_source[SourceId.PLANNING_ZONING] is EvidenceStatus.UNKNOWN
    assert by_source[SourceId.ENVIRONMENTAL_REGISTRY] is EvidenceStatus.FOUND
    assert world.adapters[SourceId.UTILITY_RTO].calls == 1


# --- Provenance & findings --------------------------------------------------


def test_every_attempt_persists_full_provenance(make_eworld: MakeEWorld) -> None:
    planning = FakeAdapter(SourceId.PLANNING_ZONING, error=RuntimeError("boom"))
    world = make_eworld(planning=planning)
    ledger = world.service.run(_manifest())
    for record in ledger.records:
        assert record.request
        assert record.raw_hash.startswith("sha256:")
        assert record.query_geometry_hash.startswith("sha256:")
        assert record.parser_version
        assert record.crs == "EPSG:4326"
        assert record.retrieved_at == BASE


def test_every_finding_cites_evidence(make_eworld: MakeEWorld) -> None:
    world = make_eworld()
    ledger = world.service.run(_manifest())
    record_ids = {r.record_id for r in ledger.records}
    assert ledger.findings
    for finding in ledger.findings:
        assert finding.evidence_record_ids
        assert set(finding.evidence_record_ids) <= record_ids


def test_unknown_is_an_exclusion_not_green(make_eworld: MakeEWorld) -> None:
    planning = FakeAdapter(SourceId.PLANNING_ZONING, error=RuntimeError("boom"))
    world = make_eworld(planning=planning)
    ledger = world.service.run(_manifest())
    finding = next(f for f in ledger.findings if f.source_id is SourceId.PLANNING_ZONING)
    assert finding.status is EvidenceStatus.UNKNOWN
    assert finding.is_exclusion is True
    assert all(_finding_is_not_green(f) for f in ledger.findings if f.is_exclusion)


def _finding_is_not_green(finding: Finding) -> bool:
    return finding.status is not EvidenceStatus.FOUND


def test_deterministic_given_fixed_fakes(make_eworld: MakeEWorld) -> None:
    first = make_eworld().service.run(_manifest())
    second = make_eworld().service.run(_manifest())
    assert first.records == second.records
    assert first.findings == second.findings
