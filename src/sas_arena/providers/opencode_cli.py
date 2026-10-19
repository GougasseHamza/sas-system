from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..models import AgentResult, Task
from ..prompts import PromptComposer
from .base import ProgressSink, Provider, find_string
from .command import resolve_executable, run_streaming_command


class OpenCodeCliProvider(Provider):
    """Full OpenCode coding-agent harness, normally backed by DeepSeek."""

    name = "deepseek"
    family = "deepseek"

    def __init__(
        self,
        *,
        command: str = "opencode",
        model: str = "deepseek/deepseek-v4-flash",
        agent: str = "",
        max_concurrency: int = 4,
        timeout_seconds: int = 600,
        standalone: bool = False,
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
        self.agent = agent
        self.standalone = standalone

    async def run(
        self,
        task: Task,
        workspace: Path,
        schema_path: Path,
        progress_sink: ProgressSink | None = None,
    ) -> AgentResult:
        started = time.monotonic()
        executable = resolve_executable(
            self.command, "~/.opencode/bin/opencode", "~/.local/bin/opencode"
        )
        if executable is None:
            raise RuntimeError(f"OpenCode executable not found: {self.command}")

        schema = json.loads(schema_path.read_text())
        prompt = (
            self.prompt(task, workspace)
            + "\n\nYou are running as a full coding agent: inspect files, edit when "
            "the task authorizes writes, and execute the commands needed to prove "
            "your result. End with only one JSON object matching this schema:\n"
            + json.dumps(schema, separators=(",", ":"))
        )
        argv = [executable, "run", "--format", "json", "--auto"]
        if self.standalone:
            argv.append("--standalone")
        if self.model:
            argv.extend(["--model", self.model])
        if self.agent:
            argv.extend(["--agent", self.agent])
        session_id = task.context.get("deepseek_session_id")
        if session_id:
            argv.extend(["--session", str(session_id)])
        else:
            argv.extend(
                ["--title", f"arena:{task.service}:{task.kind.value}:{task.id[:8]}"]
            )

        envelopes: list[dict[str, Any]] = []
        text_parts: list[str] = []

        async def observe(line: str) -> None:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if not isinstance(event, dict):
                return
            envelopes.append(event)
            text = _opencode_text(event)
            if text:
                text_parts.append(text)
            summary = _opencode_progress(event)
            if summary and progress_sink is not None:
                await progress_sink(task, self.family, summary)

        output = await run_streaming_command(
            argv,
            cwd=workspace,
            env=self.isolated_environment(self.family),
            stdin=prompt,
            timeout_seconds=min(task.deadline_seconds, self.timeout_seconds),
            stdout_line_sink=observe,
        )
        if output.returncode != 0:
            raise RuntimeError(f"OpenCode failed: {output.stderr[-2000:]}")
        errors = [
            event.get("error") for event in envelopes if event.get("type") == "error"
        ]
        if errors:
            raise RuntimeError(f"OpenCode failed: {json.dumps(errors[-1])[-2000:]}")

        payload = _structured_payload(envelopes)
        if payload is None:
            payload = self.parse_json_object("".join(text_parts).strip())
        discovered_session = session_id or find_string(
            envelopes, {"session_id", "sessionId", "sessionID"}
        )
        usage = _find_dict(envelopes, {"usage", "tokens"}) or {}
        return AgentResult.from_payload(
            self.instance_id,
            payload,
            model=self.model,
            session_id=discovered_session,
            latency_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
            raw_output=output.stdout,
        )


def _structured_payload(envelopes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(envelopes):
        for key in ("structured_output", "structuredOutput", "result"):
            value = event.get(key)
            if _is_agent_payload(value):
                return value
            if isinstance(value, str):
                try:
                    parsed = Provider.parse_json_object(value)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _is_agent_payload(parsed):
                    return parsed
        text = _opencode_text(event)
        if text:
            try:
                parsed = Provider.parse_json_object(text)
            except (json.JSONDecodeError, TypeError):
                continue
            if _is_agent_payload(parsed):
                return parsed
    return None


def _is_agent_payload(value: Any) -> bool:
    return isinstance(value, dict) and {"status", "summary", "confidence"} <= set(value)


def _opencode_text(event: dict[str, Any]) -> str:
    part = event.get("part")
    if isinstance(part, dict) and part.get("type") == "text":
        return str(part.get("text") or "")
    if event.get("type") in {"text", "message"}:
        return str(event.get("text") or event.get("content") or "")
    return ""


def _opencode_progress(event: dict[str, Any]) -> str | None:
    """Return public activity only; never forward reasoning/thinking content."""
    event_type = str(event.get("type") or "")
    if event_type in {"reasoning", "thinking", "text-delta", "text_delta"}:
        return None
    part = event.get("part")
    if isinstance(part, dict):
        part_type = str(part.get("type") or "")
        if part_type in {"reasoning", "thinking"}:
            return None
        if part_type == "tool":
            tool = str(part.get("tool") or part.get("name") or "tool")
            state = part.get("state")
            status = str(state.get("status") or "") if isinstance(state, dict) else ""
            return f"tool {tool} {status}".strip()
        if part_type in {"step-start", "step-finish"}:
            return part_type
    if event_type in {"step_start", "step-start", "step_finish", "step-finish"}:
        session_id = str(
            event.get("sessionID")
            or event.get("sessionId")
            or event.get("session_id")
            or ""
        )
        prefix = f"session {session_id} " if session_id else ""
        return prefix + event_type.replace("_", "-")
    return None


def _find_dict(value: Any, keys: set[str]) -> dict[str, Any] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, dict):
                return child
            found = _find_dict(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in reversed(value):
            found = _find_dict(child, keys)
            if found is not None:
                return found
    return None
