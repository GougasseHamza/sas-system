from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .flags import FlagBroker
from .hooks import HookOutcome, HookRunner
from .specs import EventSpec, HookSpec, ServiceSpec, SpecError
from .store import ArenaStore


@dataclass(frozen=True, slots=True)
class AttackResult:
    service: str
    target: str
    outcome: HookOutcome
    flags_found: int
    flags_new: int
    submissions: tuple[HookOutcome, ...] = ()


class OffenseController:
    """Runs already-verified exploit hooks and deduplicates their flag output."""

    def __init__(
        self,
        event: EventSpec,
        store: ArenaStore,
        runner: HookRunner,
        broker: FlagBroker | None = None,
    ) -> None:
        self.event = event
        self.store = store
        self.runner = runner
        self.broker = broker or FlagBroker(event, store, runner)

    async def attack(
        self,
        service: ServiceSpec,
        target: str,
        *,
        submit: bool = True,
    ) -> AttackResult:
        if not service.targets or target not in service.targets:
            raise PermissionError(
                f"target {target!r} is not declared for service {service.id!r}"
            )
        self.runner.scope.require_target(target)
        active = self.store.active_exploit(service.id)
        if "attack" not in service.hooks and active is None:
            raise SpecError(f"service {service.id!r} has no verified attack hook")
        exploit_version = ""
        if active is not None:
            artifact = _runnable_artifact(Path(str(active["artifact_path"])))
            if artifact is not None:
                exploit_version = str(active["version"])
                argv = (
                    (sys.executable, str(artifact), target)
                    if artifact.suffix == ".py"
                    else (str(artifact), target)
                )
                outcome = await self.runner.run(
                    "registered_attack",
                    HookSpec(argv, timeout_seconds=30),
                    base_dir=artifact.parent,
                    variables={"target": target, "service_id": service.id},
                )
            else:
                if "attack" not in service.hooks:
                    raise SpecError(
                        f"active exploit artifact is not runnable: "
                        f"{active['artifact_path']}"
                    )
                outcome = await self.runner.run_service(
                    service,
                    "attack",
                    {
                        "target": target,
                        "exploit_path": str(active["artifact_path"]),
                        "exploit_version": str(active["version"]),
                    },
                )
                exploit_version = str(active["version"])
        else:
            outcome = await self.runner.run_service(
                service, "attack", {"target": target}
            )
        flags = self.broker.extract(outcome.stdout)
        tick = self.store.get_event_state("tick")
        flags_new = self.broker.ingest(
            service.id,
            target,
            flags,
            exploit_version=exploit_version,
            tick="" if tick is None else str(tick),
        )
        submissions: list[HookOutcome] = []
        if submit:
            submissions.extend(
                await self.broker.submit_once(service=service.id, target=target)
            )
        self.store.record_service_event(
            service.id,
            "offense.attack_run",
            {
                "target": target,
                "outcome": outcome.packet(),
                "flags_found": len(flags),
                "flags_new": flags_new,
                "exploit_version": exploit_version,
                "submissions": [item.packet() for item in submissions],
            },
            severity="info" if outcome.passed else "warning",
        )
        return AttackResult(
            service.id,
            target,
            outcome,
            len(flags),
            flags_new,
            tuple(submissions),
        )


def _runnable_artifact(path: Path) -> Path | None:
    path = path.resolve()
    if path.is_file() and (path.suffix == ".py" or os.access(path, os.X_OK)):
        return path
    if path.is_dir():
        for name in ("exploit.py", "run", "exploit"):
            candidate = path / name
            if candidate.is_file() and (
                candidate.suffix == ".py" or os.access(candidate, os.X_OK)
            ):
                return candidate
    return None
