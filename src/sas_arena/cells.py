from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass
from typing import Any

from .flags import FlagBroker
from .monitoring import MonitorEngine, MonitorResult
from .observation import EventObserver
from .offense import AttackResult, OffenseController
from .orchestrator import Orchestrator
from .phases import PhaseController, current_profile, phase_allows
from .specs import ServiceSpec
from .store import ArenaStore
from .traffic import TrafficEngine, TrafficPollResult


@dataclass(frozen=True, slots=True)
class CellCycleResult:
    service: str
    monitors: tuple[MonitorResult, ...]
    traffic: tuple[TrafficPollResult, ...]
    processed_task: bool
    attacks: tuple[AttackResult, ...]


class ServiceCell:
    """One isolated logical agentic system for one competition service."""

    def __init__(
        self,
        service: ServiceSpec,
        store: ArenaStore,
        orchestrator: Orchestrator,
        monitor: MonitorEngine,
        traffic: TrafficEngine,
        offense: OffenseController,
        *,
        tick_seconds: float,
        targets: tuple[str, ...] | None = None,
        tick_driven: bool = False,
        max_workers: int | None = None,
    ) -> None:
        self.service = service
        self.store = store
        self.orchestrator = orchestrator
        self.monitor = monitor
        self.traffic = traffic
        self.offense = offense
        self.tick_seconds = tick_seconds
        self.targets = service.targets if targets is None else targets
        self.tick_driven = tick_driven
        self.max_workers = max_workers or service.worker_count

    async def run_once(self) -> CellCycleResult:
        monitor_results = (
            await self.monitor.run_once(services=[self.service.id])
            if self._allows("monitoring_enabled", True)
            else []
        )
        traffic_results = (
            await self.traffic.run_once(services=[self.service.id])
            if self._allows("traffic_enabled", True)
            else []
        )
        processed = await self.orchestrator.run_once(
            worker=f"cell-{self.service.id}-once"
        )
        attacks: list[AttackResult] = []
        if self.service.offense_enabled and self._allows("offense_enabled", True):
            attacks = await self._attack_all()
        return CellCycleResult(
            self.service.id,
            tuple(monitor_results),
            tuple(traffic_results),
            processed,
            tuple(attacks),
        )

    async def run_forever(self) -> None:
        stop = asyncio.Event()
        jobs = [
            asyncio.create_task(
                self.monitor.run_forever(
                    services=[self.service.id],
                    stop=stop,
                    enabled=lambda: self._allows("monitoring_enabled", True),
                )
            ),
            asyncio.create_task(
                self.orchestrator.run_pool(
                    workers=self.max_workers,
                    idle_exit_seconds=0,
                    active_workers=self._active_workers,
                )
            ),
        ]
        if self.service.traffic is not None:
            jobs.append(
                asyncio.create_task(
                    self.traffic.run_forever(
                        services=[self.service.id],
                        stop=stop,
                        enabled=lambda: self._allows("traffic_enabled", True),
                    )
                )
            )
        if self.service.offense_enabled:
            jobs.append(asyncio.create_task(self._offense_loop(stop)))
        try:
            await asyncio.gather(*jobs)
        finally:
            stop.set()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def _offense_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not self._allows("offense_enabled", True):
                try:
                    await asyncio.wait_for(stop.wait(), timeout=0.5)
                except TimeoutError:
                    pass
                continue
            await self._attack_all()
            if self.tick_driven:
                await self._wait_for_tick_change(stop)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.tick_seconds)
                except TimeoutError:
                    pass

    async def _wait_for_tick_change(self, stop: asyncio.Event) -> None:
        current = self.store.get_event_state("tick")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.tick_seconds * 1.5
        while not stop.is_set() and loop.time() < deadline:
            await asyncio.sleep(min(0.25, self.tick_seconds / 10))
            observed = self.store.get_event_state("tick")
            if observed is not None and observed != current:
                return

    async def _attack_all(self) -> list[AttackResult]:
        results = await asyncio.gather(
            *(self._attack_target(target) for target in self.targets)
        )
        return [result for result in results if result is not None]

    async def _attack_target(self, target: str) -> AttackResult | None:
        try:
            return await self.offense.attack(self.service, target, submit=False)
        except Exception as error:  # noqa: BLE001 - isolate one target failure
            self.store.record_service_event(
                self.service.id,
                "offense.attack_error",
                {"target": target, "error": str(error)},
                severity="error",
            )
            return None

    def _allows(self, capability: str, default: bool) -> bool:
        return phase_allows(self.store, capability, default=default)

    def _active_workers(self) -> int:
        profile = current_profile(self.store)
        return max(1, int(profile.get("cell_workers", self.service.worker_count)))


class CellSupervisor:
    def __init__(
        self,
        cells: list[ServiceCell],
        broker: FlagBroker | None = None,
        observer: EventObserver | None = None,
        phase_controller: PhaseController | None = None,
        synchronizer: Any | None = None,
    ) -> None:
        self.cells = cells
        self.broker = broker
        self.observer = observer
        self.phase_controller = phase_controller
        self.synchronizer = synchronizer

    async def run_once(self) -> list[CellCycleResult]:
        if self.phase_controller is not None:
            self.phase_controller.snapshot()
        if self.synchronizer is not None:
            await self.synchronizer.poll_once()
        if self.observer is not None:
            await self.observer.poll_once()
        results = list(await asyncio.gather(*(cell.run_once() for cell in self.cells)))
        if self.broker is not None and phase_allows(
            self.broker.store, "submission_enabled", default=True
        ):
            await self.broker.submit_once()
        return results

    async def run_forever(self) -> None:
        jobs = [asyncio.create_task(cell.run_forever()) for cell in self.cells]
        if self.observer is not None:
            jobs.append(asyncio.create_task(self.observer.run_forever()))
        if self.phase_controller is not None:
            jobs.append(asyncio.create_task(self._phase_loop()))
        if self.synchronizer is not None:
            jobs.append(asyncio.create_task(self.synchronizer.run_forever()))
        if self.broker is not None:
            jobs.append(asyncio.create_task(self._submission_loop()))
        try:
            await asyncio.gather(*jobs)
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def close(self) -> None:
        providers = {
            provider
            for cell in self.cells
            for provider in cell.orchestrator.providers.values()
        }
        await asyncio.gather(
            *(provider.close() for provider in providers), return_exceptions=False
        )

    async def _submission_loop(self) -> None:
        assert self.broker is not None
        while True:
            if phase_allows(self.broker.store, "submission_enabled", default=True):
                await self.broker.submit_once()
            await asyncio.sleep(self.broker.event.submission_interval_seconds)

    async def _phase_loop(self) -> None:
        assert self.phase_controller is not None
        while True:
            self.phase_controller.snapshot()
            await asyncio.sleep(min(1.0, self.phase_controller.event.tick_seconds / 4))


def shard_targets(
    service: str,
    targets: tuple[str, ...],
    *,
    shard_index: int,
    shard_count: int,
) -> tuple[str, ...]:
    """Deterministically divide a service target set between farm instances."""
    if shard_count <= 0 or shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be in [0, shard_count)")
    return tuple(
        target
        for target in targets
        if zlib.crc32(f"{service}:{target}".encode()) % shard_count == shard_index
    )
