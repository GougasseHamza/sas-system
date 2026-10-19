from __future__ import annotations

import abc
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..models import AgentResult, Task
from ..prompts import PromptComposer

ProgressSink = Callable[[Task, str, str], Awaitable[None]]


class Provider(abc.ABC):
    name: str
    family: str

    def __init__(
        self,
        *,
        max_concurrency: int = 0,
        timeout_seconds: int = 600,
        prompt_composer: PromptComposer | None = None,
        instance_id: str | None = None,
    ) -> None:
        if max_concurrency < 0:
            raise ValueError("max_concurrency must be zero (unbounded) or positive")
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds
        self.prompt_composer = prompt_composer or PromptComposer()
        default_name = getattr(type(self), "name", instance_id or "provider")
        self.family = getattr(type(self), "family", default_name)
        self.instance_id = instance_id or default_name
        self._semaphore = (
            None if max_concurrency == 0 else asyncio.Semaphore(max_concurrency)
        )

    async def execute(
        self,
        task: Task,
        workspace: Path,
        schema_path: Path,
        progress_sink: ProgressSink | None = None,
    ) -> AgentResult:
        if self._semaphore is None:
            return await self.run(task, workspace, schema_path, progress_sink)
        async with self._semaphore:
            return await self.run(task, workspace, schema_path, progress_sink)

    @abc.abstractmethod
    async def run(
        self,
        task: Task,
        workspace: Path,
        schema_path: Path,
        progress_sink: ProgressSink | None = None,
    ) -> AgentResult:
        raise NotImplementedError

    async def close(self) -> None:
        """Release provider resources. Stateless providers have nothing to close."""

    def prompt(self, task: Task, workspace: Path) -> str:
        return self.prompt_composer.render(task, workspace)

    @staticmethod
    def codex_checkpoint_protocol() -> str:
        return """
Parallel-dispatch protocol:
Within your first source-inspection pass, call the `sas_dispatch.dispatch_task`
tool for each concrete branch that Claude or DeepSeek can test without blocking
your main investigation. The tool returns immediately; keep working on the main
branch and do not wait for child results. Prefer the tool over prose checkpoints.
After continuing your own analysis, call `sas_dispatch.get_worker_updates` once
before finalizing so completed fast-worker evidence can inform your conclusion.

If the tool is unavailable, emit one public progress line in exactly this form
before continuing your own work:
DISPATCH_HINT: {"kind":"vuln_scout","goal":"bounded test to run","evidence":[{"path":"relative/file","line":1}],"priority":0.7,"write_access":false}
Allowed kinds: source_map, checker_map, traffic_triage, traffic_reconstruct,
reverse_exploit, vuln_scout, exploit_build, exploit_verify, patch_build,
patch_verify, incident_response, diagnose.
The goal must be executable independently and evidence must contain only facts
you actually observed. Do not expose private chain-of-thought. Emit at most one
hint per independent branch and keep pursuing the main task after emitting it.
""".strip()

    @staticmethod
    def parse_json_object(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        value = json.loads(stripped)
        if not isinstance(value, dict):
            raise TypeError("provider output is not a JSON object")
        return value

    @staticmethod
    def isolated_environment(provider: str) -> dict[str, str]:
        env = dict(os.environ)
        if provider == "codex":
            for key in list(env):
                if key.startswith(("ANTHROPIC_", "DEEPSEEK_")):
                    env.pop(key, None)
        elif provider == "claude":
            # Force the subscription login path; API credentials silently override it.
            for key in list(env):
                if key.startswith(("OPENAI_", "DEEPSEEK_")) or key in {
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                }:
                    env.pop(key, None)
        elif provider == "deepseek":
            for key in list(env):
                if key.startswith(("OPENAI_", "ANTHROPIC_")):
                    env.pop(key, None)
        return env


def find_string(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, str):
                return child
            found = find_string(child, keys)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_string(child, keys)
            if found:
                return found
    return None
