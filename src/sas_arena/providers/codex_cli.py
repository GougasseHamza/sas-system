from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from ..models import AgentResult, Task
from ..prompts import PromptComposer
from .base import ProgressSink, Provider, find_string
from .command import run_streaming_command


class CodexCliProvider(Provider):
    name = "codex"
    family = "codex"

    def __init__(
        self,
        *,
        command: str = "codex",
        model: str = "",
        reasoning_effort: str = "",
        max_concurrency: int = 0,
        timeout_seconds: int = 900,
        dispatch_database: Path | None = None,
        dispatch_max_children: int = 6,
        dispatch_child_deadline_seconds: int = 180,
        prompt_composer: PromptComposer | None = None,
        instance_id: str | None = None,
    ) -> None:
        super().__init__(
            max_concurrency=max_concurrency,
            timeout_seconds=timeout_seconds,
            prompt_composer=prompt_composer,
            instance_id=instance_id,
        )
        self.command = command
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.dispatch_database = dispatch_database
        self.dispatch_max_children = dispatch_max_children
        self.dispatch_child_deadline_seconds = dispatch_child_deadline_seconds

    async def run(
        self,
        task: Task,
        workspace: Path,
        schema_path: Path,
        progress_sink: ProgressSink | None = None,
    ) -> AgentResult:
        started = time.monotonic()
        write_access = bool(task.context.get("write_access", False))
        session_id = task.context.get("codex_session_id")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            output_path = Path(handle.name)

        if session_id:
            argv = [
                self.command,
                "exec",
                "resume",
                "--json",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if self.model:
                argv.extend(["--model", self.model])
            argv.extend([str(session_id), "-"])
        else:
            argv = [
                self.command,
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write" if write_access else "read-only",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if write_access:
                argv.append("--approve-for-me")
            if self.model:
                argv.extend(["--model", self.model])
            argv.append("-")

        if self.dispatch_database is not None:
            if "--sandbox" in argv:
                sandbox_at = argv.index("--sandbox")
                del argv[sandbox_at : sandbox_at + 2]
            dispatch_args = [
                "-m",
                "sas_arena.mcp_dispatch",
                "--database",
                str(self.dispatch_database),
                "--parent-task",
                task.id,
                "--max-children",
                str(self.dispatch_max_children),
                "--child-deadline-seconds",
                str(self.dispatch_child_deadline_seconds),
            ]
            # Scope this MCP tool to a single run; never mutate global Codex config.
            argv[2:2] = [
                "-c",
                f"mcp_servers.sas_dispatch.command={json.dumps(sys.executable)}",
                "-c",
                "mcp_servers.sas_dispatch.args=" + json.dumps(dispatch_args),
            ]
            if "--approve-for-me" not in argv:
                argv[2:2] = ["--approve-for-me"]
        if self.reasoning_effort:
            argv[2:2] = [
                "-c",
                "model_reasoning_effort=" + json.dumps(self.reasoning_effort),
            ]

        try:

            async def on_line(line: str) -> None:
                if progress_sink is None or "DISPATCH_HINT:" not in line:
                    return
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = line
                for text in _strings_with_dispatch_hint(event):
                    await progress_sink(task, self.family, text)

            output = await run_streaming_command(
                argv,
                cwd=workspace,
                env=self.isolated_environment(self.name),
                stdin=(
                    self.prompt(task, workspace)
                    + "\n\n"
                    + self.codex_checkpoint_protocol()
                ),
                timeout_seconds=min(task.deadline_seconds, self.timeout_seconds),
                stdout_line_sink=on_line,
            )
            if output.returncode != 0:
                detail = (output.stderr + "\n" + output.stdout)[-4000:]
                raise RuntimeError(f"Codex failed: {detail.strip()}")
            final_text = output_path.read_text() if output_path.exists() else ""
            payload = self.parse_json_object(final_text)
            parsed_events = []
            for line in output.stdout.splitlines():
                try:
                    parsed_events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            discovered_session = session_id or find_string(
                parsed_events, {"thread_id", "threadId", "session_id", "sessionId"}
            )
            return AgentResult.from_payload(
                self.instance_id,
                payload,
                model=self.model or None,
                session_id=discovered_session,
                latency_ms=int((time.monotonic() - started) * 1000),
                raw_output=output.stdout,
            )
        finally:
            output_path.unlink(missing_ok=True)


def _strings_with_dispatch_hint(value):
    if isinstance(value, str):
        if "DISPATCH_HINT:" in value:
            yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings_with_dispatch_hint(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings_with_dispatch_hint(child)
