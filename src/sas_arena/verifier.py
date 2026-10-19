from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import ClassVar

from .hooks import HookRunner
from .models import AgentResult, Task, TaskKind, VerificationResult
from .specs import EventSpec


class CommandVerifier:
    async def verify(
        self, task: Task, result: AgentResult, workspace: Path
    ) -> VerificationResult:
        verification = task.context.get("verification", {})
        commands = verification.get("commands", [])
        checks: list[dict] = []

        if not commands:
            passed = result.status in {"candidate", "completed"} and bool(result.claims)
            return VerificationResult(
                passed=passed,
                summary="structural result gate passed"
                if passed
                else "structural result gate failed",
                checks=[{"type": "structural", "passed": passed}],
            )

        for command in commands:
            if not isinstance(command, list) or not command:
                return VerificationResult(False, "invalid verifier command", checks)
            rendered = [
                str(part)
                .replace("{workspace}", str(workspace))
                .replace("{arena_root}", str(Path(__file__).resolve().parents[2]))
                for part in command
            ]
            process = await asyncio.create_subprocess_exec(
                *rendered,
                cwd=str(workspace),
                env=self._environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=min(task.deadline_seconds, 300)
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                checks.append(
                    {"command": rendered, "passed": False, "reason": "timeout"}
                )
                return VerificationResult(False, "verification timed out", checks)
            passed = process.returncode == 0
            checks.append(
                {
                    "command": rendered,
                    "passed": passed,
                    "stdout": stdout.decode(errors="replace")[-4000:],
                    "stderr": stderr.decode(errors="replace")[-4000:],
                }
            )
            if not passed:
                return VerificationResult(False, "verification command failed", checks)
        return VerificationResult(True, "all verification commands passed", checks)

    @staticmethod
    def _environment() -> dict[str, str]:
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(("OPENAI_", "ANTHROPIC_", "DEEPSEEK_", "CODEX_")):
                env.pop(key, None)
        return env


class ArenaVerifier(CommandVerifier):
    """Adds service-declared exploit/patch proof gates to result verification."""

    REQUIRED_HOOKS: ClassVar[dict[TaskKind, str]] = {
        TaskKind.EXPLOIT_BUILD: "verify_exploit",
        TaskKind.EXPLOIT_VERIFY: "verify_exploit",
        TaskKind.REVERSE_EXPLOIT: "verify_exploit",
        TaskKind.PATCH_BUILD: "verify_patch",
        TaskKind.PATCH_VERIFY: "verify_patch",
        TaskKind.INCIDENT_RESPONSE: "verify_patch",
    }

    def __init__(self, event: EventSpec, runner: HookRunner) -> None:
        self.event = event
        self.runner = runner

    async def verify(
        self, task: Task, result: AgentResult, workspace: Path
    ) -> VerificationResult:
        baseline = await super().verify(task, result, workspace)
        if not baseline.passed:
            return baseline
        hook_name = self.REQUIRED_HOOKS.get(task.kind)
        if hook_name is None:
            return baseline
        service = self.event.service(task.service)
        if hook_name not in service.hooks:
            return VerificationResult(
                False,
                f"service descriptor is missing mandatory {hook_name} hook",
                baseline.checks
                + [{"type": "service_hook", "hook": hook_name, "passed": False}],
            )
        outcome = await self.runner.run_service(
            service,
            hook_name,
            {
                "candidate_root": str(workspace),
                "workspace": str(workspace),
                "revision": str(task.context.get("service_revision", "candidate")),
            },
        )
        checks = baseline.checks + [outcome.packet()]
        return VerificationResult(
            outcome.passed,
            f"{hook_name} passed" if outcome.passed else f"{hook_name} failed",
            checks,
        )
