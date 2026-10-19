from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ..models import AgentResult, Task
from ..prompts import PromptComposer
from .base import ProgressSink, Provider, find_string
from .command import resolve_executable, run_streaming_command


class ClaudeCliProvider(Provider):
    name = "claude"
    family = "claude"

    def __init__(
        self,
        *,
        command: str = "claude",
        model: str = "sonnet",
        max_concurrency: int = 0,
        timeout_seconds: int = 600,
        dispatch_database: Path | None = None,
        control_database: Path | None = None,
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
        self.dispatch_database = dispatch_database
        self.control_database = control_database or dispatch_database
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
        executable = resolve_executable(
            self.command, "~/.local/bin/claude", "~/.claude/local/claude"
        )
        if executable is None:
            raise RuntimeError(f"Claude Code executable not found: {self.command}")
        schema_value = json.loads(schema_path.read_text())
        # Claude CLI validates a supported subset and rejects the draft URI.
        schema_value.pop("$schema", None)
        schema = json.dumps(schema_value, separators=(",", ":"))
        session_id = task.context.get("claude_session_id")

        argv = [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            schema,
            "--permission-prompts",
            "none",
            "--permission-mode",
            "auto",
            "--tools",
            "default",
            "--model",
            self.model,
        ]
        if session_id:
            argv.extend(["--resume", str(session_id)])
        mcp_config = self._mcp_config(task)
        if mcp_config is not None:
            argv.extend(["--mcp-config", mcp_config, "--strict-mcp-config"])

        envelopes: list[dict] = []

        async def observe(line: str) -> None:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if not isinstance(event, dict):
                return
            envelopes.append(event)
            summary = _claude_progress(event)
            if summary and progress_sink is not None:
                await progress_sink(task, self.family, summary)

        output = await run_streaming_command(
            argv,
            cwd=workspace,
            env=self.isolated_environment(self.family),
            stdin=self.prompt(task, workspace),
            timeout_seconds=min(task.deadline_seconds, self.timeout_seconds),
            stdout_line_sink=observe,
        )
        if output.returncode != 0:
            raise RuntimeError(f"Claude failed: {output.stderr[-2000:]}")
        if not envelopes:
            raise RuntimeError("Claude returned no JSON events")
        envelope = envelopes[-1]
        for candidate in reversed(envelopes):
            if candidate.get("type") == "result":
                envelope = candidate
                break
        if envelope.get("is_error") or envelope.get("stop_reason") == "refusal":
            detail = str(envelope.get("result") or "Claude turn failed")
            raise RuntimeError(detail[-2000:])
        final_value = envelope.get("structured_output") or envelope.get("result")
        if isinstance(final_value, str):
            payload = self.parse_json_object(final_value)
        elif isinstance(final_value, dict):
            payload = final_value
        else:
            payload = envelope
        discovered_session = session_id or find_string(
            envelope, {"session_id", "sessionId"}
        )
        usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
        return AgentResult.from_payload(
            self.instance_id,
            payload,
            model=self.model,
            session_id=discovered_session,
            latency_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
            raw_output=output.stdout,
        )

    def _mcp_config(self, task: Task) -> str | None:
        servers: dict[str, dict] = {}
        if self.dispatch_database is not None:
            servers["sas_dispatch"] = {
                "command": sys.executable,
                "args": [
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
                ],
            }
        if self.control_database is not None:
            servers["sas_control"] = {
                "command": sys.executable,
                "args": [
                    "-m",
                    "sas_arena.mcp_control",
                    "--database",
                    str(self.control_database),
                    "--parent-task",
                    task.id,
                ],
            }
        if not servers:
            return None
        return json.dumps({"mcpServers": servers}, separators=(",", ":"))


def _claude_progress(event: dict) -> str | None:
    """Expose tool activity and public text, but never thinking blocks."""
    event_type = str(event.get("type") or "")
    if event_type == "system" and event.get("subtype") == "init":
        session_id = str(event.get("session_id") or "")
        model = str(event.get("model") or "")
        return f"session {session_id} initialized model={model}".strip()
    if event_type == "assistant":
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return None
        summaries: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                summaries.append(f"tool {block.get('name', 'unknown')} started")
            elif block.get("type") == "text":
                text = str(block.get("text") or "").strip().replace("\n", " ")
                if text:
                    summaries.append(f"message {text[:240]}")
        return "; ".join(summaries) or None
    if event_type == "result":
        return "turn completed"
    return None
