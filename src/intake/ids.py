"""Opaque identifier types used across the intake package.

Each is a distinct ``NewType`` over ``str`` so the type checker prevents mixing
a client id where a case id is expected, without any runtime overhead.
"""

from typing import NewType

PrincipalId = NewType("PrincipalId", str)
ClientId = NewType("ClientId", str)
EngagementId = NewType("EngagementId", str)
CaseId = NewType("CaseId", str)
MessageId = NewType("MessageId", str)
