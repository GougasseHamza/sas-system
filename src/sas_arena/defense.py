from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .hooks import HookOutcome, HookRunner
from .specs import ServiceSpec, SpecError
from .store import ArenaStore


@dataclass(frozen=True, slots=True)
class DeploymentResult:
    deployment_id: str
    service: str
    revision: str
    status: str
    checks: tuple[HookOutcome, ...]
    rolled_back: bool


class DefenseController:
    """Applies a candidate only after proof gates and rolls back failed canaries."""

    def __init__(self, store: ArenaStore, runner: HookRunner) -> None:
        self.store = store
        self.runner = runner

    async def deploy(
        self,
        service: ServiceSpec,
        candidate_root: str | Path,
        revision: str,
    ) -> DeploymentResult:
        candidate = self.runner.scope.require_workdir(candidate_root)
        required = ("verify_patch", "deploy", "rollback")
        missing = [name for name in required if name not in service.hooks]
        if missing:
            raise SpecError(
                f"service {service.id!r} is not deployable; missing hooks: "
                + ", ".join(missing)
            )
        variables = {"candidate_root": str(candidate), "revision": revision}
        deployment_id = self.store.record_deployment(
            service.id,
            revision,
            str(candidate),
            "verifying",
            {},
        )
        checks: list[HookOutcome] = []
        proof = await self.runner.run_service(service, "verify_patch", variables)
        checks.append(proof)
        if not proof.passed:
            self.store.record_deployment(
                service.id,
                revision,
                str(candidate),
                "rejected",
                {"checks": [check.packet() for check in checks]},
                deployment_id=deployment_id,
            )
            self._event(service, deployment_id, "deployment.rejected", checks)
            return DeploymentResult(
                deployment_id,
                service.id,
                revision,
                "rejected",
                tuple(checks),
                False,
            )

        deployed = await self.runner.run_service(service, "deploy", variables)
        checks.append(deployed)
        post_hook_names = [
            name for name in ("health", "checker") if name in service.hooks
        ]
        if deployed.passed:
            for name in post_hook_names:
                checks.append(await self.runner.run_service(service, name, variables))
        passed = deployed.passed and all(check.passed for check in checks[2:])
        if passed:
            self.store.record_deployment(
                service.id,
                revision,
                str(candidate),
                "active",
                {"checks": [check.packet() for check in checks]},
                deployment_id=deployment_id,
            )
            self._event(service, deployment_id, "deployment.activated", checks)
            return DeploymentResult(
                deployment_id,
                service.id,
                revision,
                "active",
                tuple(checks),
                False,
            )

        rollback = await self.runner.run_service(service, "rollback", variables)
        checks.append(rollback)
        status = "rolled_back" if rollback.passed else "rollback_failed"
        self.store.record_deployment(
            service.id,
            revision,
            str(candidate),
            status,
            {"checks": [check.packet() for check in checks]},
            deployment_id=deployment_id,
        )
        self._event(service, deployment_id, f"deployment.{status}", checks)
        return DeploymentResult(
            deployment_id,
            service.id,
            revision,
            status,
            tuple(checks),
            True,
        )

    def _event(
        self,
        service: ServiceSpec,
        deployment_id: str,
        event_type: str,
        checks: list[HookOutcome],
    ) -> None:
        self.store.record_service_event(
            service.id,
            event_type,
            {
                "deployment_id": deployment_id,
                "checks": [check.packet() for check in checks],
            },
            severity=(
                "info"
                if event_type == "deployment.activated"
                else "critical"
                if event_type.endswith("rollback_failed")
                else "warning"
            ),
        )
