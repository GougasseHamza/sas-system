from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hooks import HookOutcome, HookRunner
from .models import AgentResult, Risk, Task, TaskKind, TaskLane
from .specs import EventSpec, ServiceSpec, TrafficSpec
from .store import ArenaStore


@dataclass(frozen=True, slots=True)
class TrafficDetection:
    fingerprint: str
    summary: str
    score: float
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TrafficPollResult:
    service: str
    outcome: HookOutcome
    detections: tuple[TrafficDetection, ...]
    task_ids: tuple[str, ...]
    new_tasks: int
    rejected: int


class TrafficEngine:
    """Turns bounded tool detections into service-local traffic analyst tasks."""

    def __init__(
        self,
        event: EventSpec,
        store: ArenaStore,
        runner: HookRunner,
    ) -> None:
        self.event = event
        self.store = store
        self.runner = runner
        self._last_run: dict[str, float] = {}

    async def run_once(
        self,
        *,
        services: Iterable[str] | None = None,
        due_only: bool = False,
    ) -> list[TrafficPollResult]:
        selected = set(services or self.event.services)
        now = time.monotonic()
        jobs: list[tuple[ServiceSpec, TrafficSpec]] = []
        for service_id in sorted(selected):
            service = self.event.service(service_id)
            spec = service.traffic
            if spec is None:
                continue
            last = self._last_run.get(service.id, 0.0)
            if due_only and now - last < spec.interval_seconds:
                continue
            self._last_run[service.id] = now
            jobs.append((service, spec))
        return list(await asyncio.gather(*(self._poll(*item) for item in jobs)))

    async def run_forever(
        self,
        *,
        services: Iterable[str] | None = None,
        stop: asyncio.Event | None = None,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        stop = stop or asyncio.Event()
        selected = set(services or self.event.services)
        intervals = [
            service.traffic.interval_seconds
            for service_id in selected
            if (service := self.event.service(service_id)).traffic is not None
        ]
        if not intervals:
            return
        while not stop.is_set():
            if enabled is None or enabled():
                await self.run_once(services=selected, due_only=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(min(intervals), 1.0))
            except TimeoutError:
                pass

    async def _poll(
        self, service: ServiceSpec, spec: TrafficSpec
    ) -> TrafficPollResult:
        try:
            outcome = await self.runner.run_service(service, spec.hook)
        except Exception as error:  # noqa: BLE001 - collector failure is an event
            outcome = HookOutcome(
                hook=spec.hook,
                argv=(),
                returncode=-1,
                passed=False,
                stdout="",
                stderr=str(error),
                latency_ms=0,
            )
        if not outcome.passed:
            self.store.record_service_event(
                service.id,
                "traffic.collector_failed",
                {"outcome": outcome.packet()},
                severity="error",
            )
            return TrafficPollResult(service.id, outcome, (), (), 0, 0)

        raw, rejected = _parse_output(outcome.stdout, spec.max_detections)
        detections: list[TrafficDetection] = []
        task_ids: list[str] = []
        new_tasks = 0
        for value in raw:
            detection = _normalize(value)
            if detection is None or detection.score < spec.min_score:
                rejected += 1
                continue
            detections.append(detection)
            task = self._task(service, spec, detection)
            task_id, created = self.store.enqueue(task)
            task_ids.append(task_id)
            new_tasks += int(created)
            self.store.record_service_event(
                service.id,
                "traffic.detection",
                {
                    "fingerprint": detection.fingerprint,
                    "summary": detection.summary,
                    "score": detection.score,
                    "task_id": task_id,
                    "task_created": created,
                    "evidence": detection.evidence,
                },
                severity="critical" if detection.score >= 0.9 else "warning",
            )
        return TrafficPollResult(
            service.id,
            outcome,
            tuple(detections),
            tuple(task_ids),
            new_tasks,
            rejected,
        )

    def _task(
        self,
        service: ServiceSpec,
        spec: TrafficSpec,
        detection: TrafficDetection,
    ) -> Task:
        tick = self.store.get_event_state("tick")
        window = (
            f"tick-{tick}"
            if tick is not None
            else f"window-{int(time.time() // spec.dedupe_window_seconds)}"
        )
        digest = hashlib.sha256(detection.fingerprint.encode()).hexdigest()[:16]
        return Task(
            service=service.id,
            kind=TaskKind.TRAFFIC_TRIAGE,
            lane=TaskLane.DEFENSE,
            goal=(
                "Classify the detected inbound traffic pattern against the checker, "
                "service behavior, and known traffic. Escalate only concrete suspicious "
                "evidence to the separate reconstruction layer."
            ),
            workdir=str(service.root),
            risk=spec.risk,
            expected_value=max(0.1, detection.score),
            deadline_seconds=spec.deadline_seconds,
            context={
                "role": "traffic-analyst",
                "captain_session": True,
                "traffic_pipeline_stage": "triage",
                "write_access": False,
                "service_revision": str(service.context.get("revision", "unknown")),
                "service_descriptor": service.public_context(),
                "traffic_fingerprint": detection.fingerprint,
                "untrusted_observations": [detection.evidence],
                "verification": {"commands": []},
            },
            dedupe_key=f"traffic:{service.id}:{window}:{digest}",
            preferred_provider="claude",
        )


def advance_traffic_pipeline(
    store: ArenaStore,
    task: Task,
    result: AgentResult,
    workspace: Path,
    *,
    provider_family: str,
) -> tuple[str, bool] | None:
    """Advance only proof-bearing traffic candidates through fixed model layers."""

    stage = str(task.context.get("traffic_pipeline_stage", ""))
    if stage not in {"triage", "reconstruct", "reverse"}:
        return None
    expected_family = "codex" if stage == "reverse" else "claude"
    if provider_family != expected_family:
        store.record_event(
            task.id,
            "traffic.pipeline_provider_mismatch",
            {"stage": stage, "expected": expected_family, "actual": provider_family},
        )
        return None
    if result.status != "candidate":
        store.record_event(
            task.id,
            "traffic.pipeline_closed",
            {"stage": stage, "status": result.status, "summary": result.summary[:1000]},
        )
        return None
    if stage == "reverse":
        store.record_event(
            task.id,
            "traffic.pipeline_completed",
            {"stage": stage, "summary": result.summary[:1000]},
        )
        return None

    shared = {
        "write_access": True,
        "service_revision": str(task.context.get("service_revision", "unknown")),
        "traffic_fingerprint": str(task.context.get("traffic_fingerprint", "")),
        "upstream_task_id": task.id,
        "untrusted_observations": [
            {
                "provider": result.provider,
                "summary": result.summary,
                "claims": [
                    {
                        "statement": claim.statement,
                        "confidence": claim.confidence,
                        "evidence": claim.evidence,
                        "next_test": claim.next_test,
                    }
                    for claim in result.claims
                ],
                "artifacts": result.artifacts,
            }
        ],
        "verification": {"commands": []},
    }
    if stage == "triage":
        next_task = Task(
            service=task.service,
            kind=TaskKind.TRAFFIC_RECONSTRUCT,
            lane=TaskLane.DEFENSE,
            goal=(
                "Reconstruct and locally replay the suspicious traffic selected by "
                "the Claude traffic analyst. Preserve exact protocol and state evidence."
            ),
            workdir=task.workdir,
            risk=Risk.HIGH,
            expected_value=max(0.75, task.expected_value),
            deadline_seconds=max(300, task.deadline_seconds),
            context={
                **shared,
                "role": "traffic-reconstructor",
                "captain_session": True,
                "traffic_pipeline_stage": "reconstruct",
            },
            parent_id=task.id,
            dedupe_key=f"traffic-reconstruct:{task.id}",
            preferred_provider="claude",
        )
    else:
        next_task = Task(
            service=task.service,
            kind=TaskKind.REVERSE_EXPLOIT,
            lane=TaskLane.OFFENSE,
            goal=(
                "Turn the Claude-proven traffic replay into a reliable verified exploit. "
                "Keep analyzing while dispatching independent branches to DeepSeek."
            ),
            workdir=str(workspace.resolve()),
            risk=Risk.CRITICAL,
            expected_value=max(0.9, task.expected_value),
            deadline_seconds=max(600, task.deadline_seconds),
            context={
                **shared,
                "role": "reverse-exploit-captain",
                "captain_session": True,
                "traffic_pipeline_stage": "reverse",
                "technical_objective": (
                    "Generalize the proven replay into a farm-ready exploit and identify "
                    "the defensive root cause."
                ),
            },
            parent_id=task.id,
            dedupe_key=f"traffic-reverse:{task.id}",
            preferred_provider="codex",
        )
    task_id, created = store.enqueue(next_task)
    store.record_event(
        task.id,
        "traffic.pipeline_advanced",
        {
            "from": stage,
            "to": str(next_task.context["traffic_pipeline_stage"]),
            "task_id": task_id,
            "created": created,
            "preferred_provider": next_task.preferred_provider,
        },
    )
    return task_id, created


def _parse_output(output: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    values: list[dict[str, Any]] = []
    rejected = 0
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            rejected += 1
            continue
        candidates: list[Any]
        if isinstance(decoded, dict) and isinstance(decoded.get("detections"), list):
            candidates = decoded["detections"]
        else:
            candidates = [decoded]
        for candidate in candidates:
            if len(values) >= limit:
                rejected += 1
            elif isinstance(candidate, dict):
                values.append(candidate)
            else:
                rejected += 1
    return values, rejected


def _normalize(value: dict[str, Any]) -> TrafficDetection | None:
    summary = value.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    try:
        score = float(value.get("score", 0))
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 1:
        return None
    bounded = _bound(value)
    fingerprint_value = value.get("fingerprint") or value.get("signature")
    if isinstance(fingerprint_value, str) and fingerprint_value.strip():
        fingerprint = fingerprint_value.strip()[:256]
    else:
        fingerprint = hashlib.sha256(
            json.dumps(bounded, sort_keys=True).encode()
        ).hexdigest()
    assert isinstance(bounded, dict)
    return TrafficDetection(fingerprint, summary.strip()[:1000], score, bounded)


def _bound(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "[truncated-depth]"
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…[truncated]"
    if isinstance(value, list):
        return [_bound(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: _bound(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]
