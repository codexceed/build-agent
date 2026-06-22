"""Shared fixtures and deterministic fakes for intake tests."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from intake.ids import CaseId, ClientId, EngagementId, PrincipalId
from intake.memory import InMemoryAuditLog, InMemoryCaseRegistry, InMemoryTriageQueue
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


@dataclasses.dataclass
class World:
    """A preloaded registry plus a wired service for tests."""

    registry: InMemoryCaseRegistry
    audit: InMemoryAuditLog
    triage: InMemoryTriageQueue
    clock: FixedClock
    service: IntakeService


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
