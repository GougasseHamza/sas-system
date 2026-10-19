from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .hooks import HookOutcome, HookRunner
from .models import Task
from .specs import EventSpec, MonitorSpec, ServiceSpec
from .store import ArenaStore


@dataclass(frozen=True, slots=True)
class MonitorResult:
    service: str
    monitor: str
    outcome: HookOutcome
    state: dict
    task_id: str | None = None
    rollback: HookOutcome | None = None


class MonitorEngine:
    """Deterministic monitor loop. Models receive incidents, never raw polling work."""

    def __init__(
        self,
        event: EventSpec,
        store: ArenaStore,
        runner: HookRunner,
    ) -> None:
        self.event = event
        self.store = store
        self.runner = runner
        self._last_run: dict[tuple[str, str], float] = {}

    async def run_once(
        self,
        *,
        services: Iterable[str] | None = None,
        due_only: bool = False,
    ) -> list[MonitorResult]:
        selected = set(services or self.event.services)
        now = time.monotonic()
        jobs: list[tuple[ServiceSpec, str, MonitorSpec]] = []
        for service_id in sorted(selected):
            service = self.event.service(service_id)
            for name, monitor in sorted(service.monitors.items()):
                last = self._last_run.get((service.id, name), 0.0)
                if due_only and now - last < monitor.interval_seconds:
                    continue
                self._last_run[(service.id, name)] = now
                jobs.append((service, name, monitor))
        return list(await asyncio.gather(*(self._run(item) for item in jobs)))

    async def run_named(self, service_id: str, monitor_name: str) -> MonitorResult:
        service = self.event.service(service_id)
        try:
            monitor = service.monitors[monitor_name]
        except KeyError as error:
            raise ValueError(
                f"service {service_id!r} has no monitor {monitor_name!r}"
            ) from error
        self._last_run[(service.id, monitor_name)] = time.monotonic()
        return await self._run((service, monitor_name, monitor))

    async def run_forever(
        self,
        *,
        services: Iterable[str] | None = None,
        stop: asyncio.Event | None = None,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            if enabled is None or enabled():
                await self.run_once(services=services, due_only=True)
            shortest = min(
                (
                    monitor.interval_seconds
                    for service in self.event.services.values()
                    for monitor in service.monitors.values()
                ),
                default=1.0,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(shortest, 1.0))
            except TimeoutError:
                pass

    async def _run(self, item: tuple[ServiceSpec, str, MonitorSpec]) -> MonitorResult:
        service, monitor_name, monitor = item
        try:
            outcome = await self.runner.run_service(service, monitor.hook)
        except Exception as error:  # noqa: BLE001 - monitor failures become signals
            outcome = HookOutcome(
                hook=monitor.hook,
                argv=(),
                returncode=-1,
                passed=False,
                stdout="",
                stderr=str(error),
                latency_ms=0,
            )
        summary = (
            f"{monitor.hook} passed"
            if outcome.passed
            else f"{monitor.hook} failed with return code {outcome.returncode}"
        )
        state = self.store.update_monitor_state(
            service.id,
            monitor_name,
            passed=outcome.passed,
            summary=summary,
            failure_threshold=monitor.failure_threshold,
            recovery_threshold=monitor.recovery_threshold,
        )
        event_type = "monitor.sample"
        severity = "info" if outcome.passed else "warning"
        if state["opened"]:
            event_type = "monitor.alert"
            severity = "critical" if monitor.risk.value == "critical" else "error"
        elif state["recovered"]:
            event_type = "monitor.recovered"
        event_id = self.store.record_service_event(
            service.id,
            event_type,
            {
                "monitor": monitor_name,
                "summary": summary,
                "outcome": outcome.packet(),
                "state": state,
            },
            severity=severity,
        )
        task_id: str | None = None
        rollback: HookOutcome | None = None
        if state["opened"]:
            task = self._incident_task(
                service, monitor_name, monitor, outcome, event_id
            )
            task_id, _ = self.store.enqueue(task)
            deployment = self.store.active_deployment(service.id)
            recent_deployment = bool(
                deployment
                and time.time() - float(deployment["updated_at"])
                <= monitor.rollback_window_seconds
            )
            if (
                monitor.auto_rollback
                and "rollback" in service.hooks
                and recent_deployment
            ):
                rollback = await self.runner.run_service(service, "rollback")
                assert deployment is not None
                self.store.record_deployment(
                    service.id,
                    str(deployment["revision"]),
                    str(deployment["candidate_root"]),
                    "rolled_back_by_monitor" if rollback.passed else "rollback_failed",
                    {
                        **dict(deployment.get("metadata", {})),
                        "monitor": monitor_name,
                        "rollback": rollback.packet(),
                    },
                    deployment_id=str(deployment["id"]),
                )
                self.store.record_service_event(
                    service.id,
                    "deployment.auto_rollback",
                    {
                        "monitor": monitor_name,
                        "trigger_event_id": event_id,
                        "outcome": rollback.packet(),
                    },
                    severity="warning" if rollback.passed else "critical",
                )
        return MonitorResult(
            service.id, monitor_name, outcome, state, task_id, rollback
        )

    @staticmethod
    def _incident_task(
        service: ServiceSpec,
        monitor_name: str,
        monitor: MonitorSpec,
        outcome: HookOutcome,
        event_id: int,
    ) -> Task:
        return Task(
            service=service.id,
            kind=monitor.task_kind,
            lane=monitor.lane,
            goal=(
                f"Diagnose and restore service after {monitor_name!r} monitor failure. "
                "Preserve checker behavior and produce a deterministically verifiable fix."
            ),
            workdir=str(service.root),
            risk=monitor.risk,
            expected_value=1.0,
            deadline_seconds=max(120, int(monitor.interval_seconds * 6)),
            context={
                "role": "service-captain",
                "operator_mode": True,
                "captain_session": True,
                "write_access": True,
                "provider_fallbacks": ["codex"],
                "service_revision": str(service.context.get("revision", "unknown")),
                "service_descriptor": service.public_context(),
                "untrusted_observations": [outcome.packet()],
                "verification": {"commands": []},
            },
            dedupe_key=f"monitor:{service.id}:{monitor_name}:{event_id}",
            preferred_provider="claude",
        )
