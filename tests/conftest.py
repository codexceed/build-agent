"""Shared fixtures and deterministic fakes for intake tests."""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable

import pytest

from intake.classification import ClassificationService
from intake.ids import CaseId, ClientId, EngagementId, PrincipalId
from intake.memory import (
    InMemoryAuditLog,
    InMemoryCaseRegistry,
    InMemoryClarificationQueue,
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
