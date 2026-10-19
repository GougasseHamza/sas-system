from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .scope import ScopePolicy
from .specs import HookSpec, ServiceSpec

_SECRET_PREFIXES = ("OPENAI_", "ANTHROPIC_", "DEEPSEEK_", "CODEX_")
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class HookOutcome:
    hook: str
    argv: tuple[str, ...]
    returncode: int
    passed: bool
    stdout: str
    stderr: str
    latency_ms: int
    timed_out: bool = False

    def packet(self) -> dict:
        return {
            "hook": self.hook,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "passed": self.passed,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-4000:],
            "latency_ms": self.latency_ms,
            "timed_out": self.timed_out,
        }


class HookRunner:
    """Runs declared argv hooks without a shell and within authorized roots."""

    def __init__(self, scope: ScopePolicy, *, output_limit: int = 16_000) -> None:
        self.scope = scope
        self.output_limit = output_limit

    async def run_service(
        self,
        service: ServiceSpec,
        hook_name: str,
        variables: Mapping[str, str] | None = None,
    ) -> HookOutcome:
        return await self.run(
            hook_name,
            service.hook(hook_name),
            base_dir=service.root,
            variables={"service_id": service.id, **(variables or {})},
        )

    async def run(
        self,
        hook_name: str,
        hook: HookSpec,
        *,
        base_dir: Path,
        variables: Mapping[str, str] | None = None,
    ) -> HookOutcome:
        base = self.scope.require_workdir(base_dir)
        values = {
            "service_root": str(base),
            "event_root": str(base),
            **{key: str(value) for key, value in (variables or {}).items()},
        }
        argv = tuple(_render(item, values) for item in hook.argv)
        cwd_value = _render(hook.cwd, values)
        cwd = Path(cwd_value)
        if not cwd.is_absolute():
            cwd = base / cwd
        cwd = self.scope.require_workdir(cwd)
        env = _clean_environment()
        env.update({key: _render(value, values) for key, value in hook.env.items()})
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=hook.timeout_seconds
            )
        except TimeoutError:
            timed_out = True
            process.terminate()
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=3
                )
            except TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
        returncode = process.returncode if process.returncode is not None else -1
        return HookOutcome(
            hook=hook_name,
            argv=argv,
            returncode=returncode,
            passed=not timed_out and returncode in hook.expected_returncodes,
            stdout=stdout.decode(errors="replace")[-self.output_limit :],
            stderr=stderr.decode(errors="replace")[-self.output_limit :],
            latency_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
        )


def _render(template: str, values: Mapping[str, str]) -> str:
    # Replace only controller-known names. Other braces may be flag syntax,
    # source code, regexes, or JSON and must remain byte-for-byte unchanged.
    return _PLACEHOLDER.sub(
        lambda match: values.get(match.group(1), match.group(0)), template
    )


def _clean_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(_SECRET_PREFIXES):
            env.pop(key, None)
    return env
