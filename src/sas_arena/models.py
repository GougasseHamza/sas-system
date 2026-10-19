from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TaskKind(StrEnum):
    SOURCE_MAP = "source_map"
    CHECKER_MAP = "checker_map"
    TRAFFIC_TRIAGE = "traffic_triage"
    TRAFFIC_RECONSTRUCT = "traffic_reconstruct"
    REVERSE_EXPLOIT = "reverse_exploit"
    VULN_SCOUT = "vuln_scout"
    EXPLOIT_BUILD = "exploit_build"
    EXPLOIT_VERIFY = "exploit_verify"
    PATCH_BUILD = "patch_build"
    PATCH_VERIFY = "patch_verify"
    INCIDENT_RESPONSE = "incident_response"
    DIAGNOSE = "diagnose"


class TaskLane(StrEnum):
    ANALYSIS = "analysis"
    OFFENSE = "offense"
    DEFENSE = "defense"
    OPERATIONS = "operations"


class Risk(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    VERIFYING = "verifying"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    FAILED = "failed"


@dataclass(slots=True)
class Task:
    service: str
    kind: TaskKind
    goal: str
    workdir: str
    lane: TaskLane = TaskLane.ANALYSIS
    risk: Risk = Risk.NORMAL
    expected_value: float = 0.5
    deadline_seconds: int = 300
    max_attempts: int = 2
    context: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    dedupe_key: str | None = None
    attempts: int = 0
    preferred_provider: str | None = None
    parent_id: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.expected_value <= 1:
            raise ValueError("expected_value must be between 0 and 1")
        if self.deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        if self.max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if self.dedupe_key is None:
            self.dedupe_key = f"{self.service}:{self.kind}:{self.goal.strip()}"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["kind"] = self.kind.value
        value["lane"] = self.lane.value
        value["risk"] = self.risk.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Task:
        data = dict(value)
        data["kind"] = TaskKind(data["kind"])
        data["lane"] = TaskLane(data.get("lane", TaskLane.ANALYSIS))
        data["risk"] = Risk(data.get("risk", Risk.NORMAL))
        return cls(**data)


@dataclass(slots=True)
class Claim:
    statement: str
    confidence: float
    evidence: list[dict[str, Any]]
    next_test: str

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("claim confidence must be between 0 and 1")


@dataclass(slots=True)
class AgentResult:
    provider: str
    status: str
    summary: str
    confidence: float
    claims: list[Claim] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    model: str | None = None
    session_id: str | None = None
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    raw_output: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("result confidence must be between 0 and 1")

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        value = asdict(self)
        if not include_raw:
            value.pop("raw_output", None)
        return value

    @classmethod
    def from_payload(
        cls,
        provider: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        session_id: str | None = None,
        latency_ms: int = 0,
        usage: dict[str, Any] | None = None,
        raw_output: str | None = None,
    ) -> AgentResult:
        claims = [Claim(**claim) for claim in payload.get("claims", [])]
        return cls(
            provider=provider,
            model=model,
            session_id=session_id,
            latency_ms=latency_ms,
            usage=usage or {},
            raw_output=raw_output,
            status=payload["status"],
            summary=payload["summary"],
            confidence=float(payload["confidence"]),
            claims=claims,
            artifacts=list(payload.get("artifacts", [])),
            next_actions=list(payload.get("next_actions", [])),
        )


@dataclass(slots=True)
class VerificationResult:
    passed: bool
    summary: str
    checks: list[dict[str, Any]] = field(default_factory=list)
