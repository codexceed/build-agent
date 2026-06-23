"""Shared fixtures and deterministic fakes for intake tests."""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable

import pytest

from intake.classification import ClassificationService
from intake.evidence import EvidenceService
from intake.evidence_model import (
    AdapterResult,
    Coverage,
    EvidenceQuery,
    EvidenceStatus,
    SourceId,
)
from intake.ids import CaseId, ClientId, EngagementId, PrincipalId
from intake.memory import (
    InMemoryAuditLog,
    InMemoryCaseRegistry,
    InMemoryClarificationQueue,
    InMemoryEvidenceStore,
    InMemoryTriageQueue,
)
from intake.model import ClassificationProposal, MessageProjection
from intake.service import IntakeService

BASE = dt.datetime(2026, 6, 22, 12, 0, tzinfo=dt.UTC)


class FixedClock:
    """A clock that returns a settable, fixed instant."""

    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now

    def set(self, now: dt.datetime) -> None:
        self._now = now


class SequentialIdGenerator:
    """An id generator that yields deterministic ``id-1``, ``id-2`` … values."""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._n = 0

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}-{self._n}"


class FakeClassifier:
    """A classifier that returns a preset proposal, or raises on demand."""

    def __init__(
        self, proposal: ClassificationProposal | None = None, error: bool = False
    ) -> None:
        self._proposal = proposal
        self._error = error
        self.calls = 0

    def classify(self, projection: MessageProjection) -> ClassificationProposal:
        del projection
        self.calls += 1
        if self._error:
            raise RuntimeError("classifier boom")
        assert self._proposal is not None
        return self._proposal


class FakeAdapter:
    """A source adapter returning a preset result, or raising on demand."""

    def __init__(
        self,
        source_id: SourceId,
        result: AdapterResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._source_id = source_id
        self._result = result
        self._error = error
        self.calls = 0
        self.query: EvidenceQuery | None = None

    @property
    def source_id(self) -> SourceId:
        return self._source_id

    def fetch(self, query: EvidenceQuery) -> AdapterResult:
        self.calls += 1
        self.query = query
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return AdapterResult(
            source_id=self._source_id,
            status=EvidenceStatus.NOT_FOUND,
            coverage=Coverage.COMPLETE,
            request=f"req:{self._source_id.value}",
            raw="raw",
            parser_version="v1",
            crs=query.crs,
        )


@dataclasses.dataclass
class EWorld:
    """A preloaded registry plus a wired evidence service for tests."""

    registry: InMemoryCaseRegistry
    store: InMemoryEvidenceStore
    clock: FixedClock
    adapters: dict[SourceId, FakeAdapter]
    service: EvidenceService


@dataclasses.dataclass
class World:
    """A preloaded registry plus a wired service for tests."""

    registry: InMemoryCaseRegistry
    audit: InMemoryAuditLog
    triage: InMemoryTriageQueue
    clock: FixedClock
    service: IntakeService


@dataclasses.dataclass
class CWorld:
    """A preloaded registry plus a wired classification service for tests."""

    registry: InMemoryCaseRegistry
    audit: InMemoryAuditLog
    triage: InMemoryTriageQueue
    clarifications: InMemoryClarificationQueue
    classifier: FakeClassifier
    clock: FixedClock
    service: ClassificationService


@pytest.fixture
def world() -> World:
    """Standard chains: client C owns engagement E owns CASE-1; principal P in C.

    A second, disjoint chain (C2/E2/CASE-2/P2) exists so cross-chain mixing can
    be exercised. Principal ``P-revoked`` belongs to C but is revoked at BASE.
    """
    clock = FixedClock(BASE)
    ids = SequentialIdGenerator()
    registry = InMemoryCaseRegistry()
    audit = InMemoryAuditLog(clock, ids)
    triage = InMemoryTriageQueue()

    registry.add_client(ClientId("C"))
    registry.add_client(ClientId("C2"))
    registry.add_engagement(EngagementId("E"), ClientId("C"))
    registry.add_engagement(EngagementId("E2"), ClientId("C2"))
    registry.add_principal(PrincipalId("P"), ClientId("C"))
    registry.add_principal(PrincipalId("P2"), ClientId("C2"))
    registry.add_principal(PrincipalId("P-revoked"), ClientId("C"), revoked_at=BASE)

    service = IntakeService(registry, audit, triage, clock, ids)
    # Cases are registered through the service so the mutation is audited.
    service.register_case(EngagementId("E"), CaseId("CASE-1"))
    service.register_case(EngagementId("E2"), CaseId("CASE-2"))
    return World(registry, audit, triage, clock, service)


@pytest.fixture
def make_cworld() -> Callable[..., CWorld]:
    """Return a factory building a classification CWorld with a preset proposal.

    The factory takes ``proposal`` (the fake classifier's output) and ``error``
    (whether the classifier raises). Chains mirror the ``world`` fixture.
    """

    def _make(proposal: ClassificationProposal | None = None, error: bool = False) -> CWorld:
        clock = FixedClock(BASE)
        ids = SequentialIdGenerator()
        registry = InMemoryCaseRegistry()
        audit = InMemoryAuditLog(clock, ids)
        triage = InMemoryTriageQueue()
        clarifications = InMemoryClarificationQueue()
        classifier = FakeClassifier(proposal, error)

        registry.add_client(ClientId("C"))
        registry.add_client(ClientId("C2"))
        registry.add_engagement(EngagementId("E"), ClientId("C"))
        registry.add_engagement(EngagementId("E2"), ClientId("C2"))
        registry.add_principal(PrincipalId("P"), ClientId("C"))
        registry.add_principal(PrincipalId("P2"), ClientId("C2"))
        registry.register_case(EngagementId("E"), CaseId("CASE-1"))
        registry.register_case(EngagementId("E2"), CaseId("CASE-2"))

        service = ClassificationService(
            registry, audit, triage, clarifications, classifier, clock, ids
        )
        return CWorld(registry, audit, triage, clarifications, classifier, clock, service)

    return _make


@pytest.fixture
def make_eworld() -> Callable[..., EWorld]:
    """Return a factory building an evidence EWorld with three fake adapters.

    The factory takes optional ``planning``, ``environmental``, and ``utility``
    FakeAdapters; any omitted source defaults to a complete NOT_FOUND adapter.
    Registry holds CASE-1 under engagement E (client C).
    """

    def _make(
        planning: FakeAdapter | None = None,
        environmental: FakeAdapter | None = None,
        utility: FakeAdapter | None = None,
    ) -> EWorld:
        clock = FixedClock(BASE)
        ids = SequentialIdGenerator()
        registry = InMemoryCaseRegistry()
        store = InMemoryEvidenceStore()
        registry.add_client(ClientId("C"))
        registry.add_engagement(EngagementId("E"), ClientId("C"))
        registry.register_case(EngagementId("E"), CaseId("CASE-1"))
        adapters = {
            SourceId.PLANNING_ZONING: planning or FakeAdapter(SourceId.PLANNING_ZONING),
            SourceId.ENVIRONMENTAL_REGISTRY: environmental
            or FakeAdapter(SourceId.ENVIRONMENTAL_REGISTRY),
            SourceId.UTILITY_RTO: utility or FakeAdapter(SourceId.UTILITY_RTO),
        }
        service = EvidenceService(registry, store, adapters, clock, ids)
        return EWorld(registry, store, clock, adapters, service)

    return _make
