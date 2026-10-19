from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    CodexConfig,
    Sandbox,
    SkillInput,
    TextInput,
)
from openai_codex._run import _collect_async_turn_result
from openai_codex.types import ReasoningEffort

from ..models import AgentResult, Task, TaskKind
from ..prompts import PromptComposer
from ..store import ArenaStore
from .base import ProgressSink, Provider


class CodexSdkProvider(Provider):
    """Persistent Codex SDK/app-server transport.

    One provider instance owns one long-lived app-server connection. Arena persists
    thread IDs separately, scoped by service, role, provider instance, and revision.
    """

    name = "codex"
    family = "codex"

    def __init__(
        self,
        *,
        codex_bin: str = "",
        auth_mode: str = "chatgpt",
        model: str = "",
        reasoning_effort: str = "",
        service_tier: str = "",
        max_concurrency: int = 0,
        timeout_seconds: int = 900,
        dispatch_database: Path | None = None,
        control_database: Path | None = None,
        dispatch_max_children: int = 6,
        dispatch_child_deadline_seconds: int = 180,
        skills_root: Path | None = None,
        prompt_composer: PromptComposer | None = None,
        instance_id: str | None = None,
        client_factory: Callable[[CodexConfig], Any] = AsyncCodex,
    ) -> None:
        super().__init__(
            max_concurrency=max_concurrency,
            timeout_seconds=timeout_seconds,
            prompt_composer=prompt_composer,
            instance_id=instance_id,
        )
        self.codex_bin = codex_bin
        if auth_mode not in {"chatgpt", "any"}:
            raise ValueError("Codex SDK auth_mode must be 'chatgpt' or 'any'")
        self.auth_mode = auth_mode
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.service_tier = service_tier
        self.dispatch_database = dispatch_database
        self.control_database = control_database or dispatch_database
        self.dispatch_max_children = dispatch_max_children
        self.dispatch_child_deadline_seconds = dispatch_child_deadline_seconds
        self.skills_root = None if skills_root is None else skills_root.resolve()
        self._client_factory = client_factory
        self._client: Any | None = None
        self._client_lock = asyncio.Lock()

    async def run(
        self,
        task: Task,
        workspace: Path,
        schema_path: Path,
        progress_sink: ProgressSink | None = None,
    ) -> AgentResult:
        started = time.monotonic()
        client = await self._get_client()
        sandbox = (
            Sandbox.workspace_write
            if bool(task.context.get("write_access", False))
            else Sandbox.read_only
        )
        session_id = task.context.get("codex_session_id")
        thread_config = self._thread_config(task)
        thread_options: dict[str, Any] = {
            "cwd": str(workspace),
            "sandbox": sandbox,
            "config": thread_config or None,
        }
        if self.model:
            thread_options["model"] = self.model
        if self.service_tier:
            thread_options["service_tier"] = self.service_tier

        if session_id:
            thread = await client.thread_resume(
                str(session_id),
                approval_mode=ApprovalMode.auto_review,
                include_turns=False,
                **thread_options,
            )
        else:
            thread = await client.thread_start(
                approval_mode=ApprovalMode.auto_review,
                developer_instructions=self.codex_checkpoint_protocol(),
                service_name="sas-agent-arena",
                **thread_options,
            )

        schema = json.loads(schema_path.read_text())
        turn_options: dict[str, Any] = {
            "cwd": str(workspace),
            "sandbox": sandbox,
            "output_schema": schema,
            "source": "sas_agent_arena",
        }
        if self.model:
            turn_options["model"] = self.model
        if self.reasoning_effort:
            turn_options["effort"] = ReasoningEffort(self.reasoning_effort)
        if self.service_tier:
            turn_options["service_tier"] = self.service_tier

        turn = await thread.turn(self._turn_input(task, workspace), **turn_options)
        if progress_sink is not None:
            await progress_sink(
                task,
                self.family,
                f"session {thread.id} turn {turn.id} started",
            )
        steering: asyncio.Task | None = None
        try:
            if progress_sink is None:
                run = turn.run()
            else:
                run = self._run_observed(turn, task, progress_sink)
                if self.control_database is not None:
                    steering = asyncio.create_task(
                        self._forward_operator_messages(turn, task, progress_sink)
                    )
            result = await asyncio.wait_for(
                run, timeout=min(task.deadline_seconds, self.timeout_seconds)
            )
        except (asyncio.CancelledError, TimeoutError):
            await _best_effort_interrupt(turn)
            raise
        finally:
            if steering is not None:
                steering.cancel()
                await asyncio.gather(steering, return_exceptions=True)

        final_response = result.final_response or ""
        payload = self.parse_json_object(final_response)
        usage = _model_payload(result.usage)
        return AgentResult.from_payload(
            self.instance_id,
            payload,
            model=self.model or None,
            session_id=thread.id,
            latency_ms=int((time.monotonic() - started) * 1000),
            usage=usage,
            raw_output=final_response,
        )

    async def _run_observed(
        self,
        turn: Any,
        task: Task,
        progress_sink: ProgressSink,
    ):
        async def stream():
            async for event in turn.stream():
                summary = _codex_progress(event)
                if summary:
                    await progress_sink(task, self.family, summary)
                yield event

        return await _collect_async_turn_result(stream(), turn_id=turn.id)

    async def _forward_operator_messages(
        self,
        turn: Any,
        task: Task,
        progress_sink: ProgressSink,
    ) -> None:
        assert self.control_database is not None
        store = ArenaStore(self.control_database)
        while True:
            messages = store.pending_agent_messages(task.id, self.instance_id)
            for item in messages:
                message_id = int(item["id"])
                try:
                    await turn.steer(str(item["message"]))
                except Exception as error:  # noqa: BLE001 - report delivery failure
                    store.finish_agent_message(message_id, error=str(error))
                else:
                    store.finish_agent_message(message_id)
                    await progress_sink(
                        task,
                        self.family,
                        f"operator message {message_id} delivered",
                    )
            await asyncio.sleep(0.5)

    async def close(self) -> None:
        async with self._client_lock:
            client, self._client = self._client, None
        if client is not None:
            await client.close()

    async def _get_client(self):
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                config = CodexConfig(
                    codex_bin=self._resolved_codex_bin(),
                    env=self._client_environment(),
                    client_name="sas_agent_arena",
                    client_title="SAS Agent Arena",
                )
                client = self._client_factory(config)
                try:
                    await client.__aenter__()
                    await self._verify_auth(client)
                except Exception:
                    await client.close()
                    raise
                self._client = client
        return self._client

    def _resolved_codex_bin(self) -> str | None:
        if not self.codex_bin:
            return None
        resolved = shutil.which(self.codex_bin)
        return resolved or str(Path(self.codex_bin).expanduser().resolve())

    def _client_environment(self) -> dict[str, str]:
        env = self.isolated_environment(self.name)
        if self.auth_mode == "chatgpt":
            for key in (
                "OPENAI_API_KEY",
                "CODEX_API_KEY",
                "OPENAI_FEDERATION_RULE_ID",
                "OPENAI_IDENTITY_TOKEN_FILE",
            ):
                env.pop(key, None)
        return env

    async def _verify_auth(self, client: Any) -> None:
        if self.auth_mode == "any":
            return
        response = await client.account()
        account = getattr(response, "account", None)
        root = getattr(account, "root", account)
        account_type = getattr(root, "type", None)
        if account_type != "chatgpt":
            raise RuntimeError(
                "Codex SDK is not authenticated with ChatGPT; run `codex login` "
                "or set auth_mode='any' only when API billing is intentional"
            )

    def _thread_config(self, task: Task) -> dict[str, Any]:
        servers: dict[str, Any] = {}
        if self.dispatch_database is not None:
            args = [
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
            servers["sas_dispatch"] = {
                "command": sys.executable,
                "args": args,
                "required": True,
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
                "required": True,
            }
        return {"mcp_servers": servers} if servers else {}

    def _turn_input(self, task: Task, workspace: Path):
        values: list[TextInput | SkillInput] = []
        skill = self._skill_for(task.kind)
        if skill is not None:
            values.append(skill)
        values.append(TextInput(self.prompt(task, workspace)))
        return values

    def _skill_for(self, kind: TaskKind) -> SkillInput | None:
        if self.skills_root is None:
            return None
        if kind in {
            TaskKind.SOURCE_MAP,
            TaskKind.CHECKER_MAP,
        }:
            name = "arena-service-bootstrap"
        elif kind in {
            TaskKind.VULN_SCOUT,
            TaskKind.EXPLOIT_BUILD,
            TaskKind.EXPLOIT_VERIFY,
            TaskKind.REVERSE_EXPLOIT,
        }:
            name = "arena-offense"
        else:
            name = "arena-defense"
        path = self.skills_root / name / "SKILL.md"
        if not path.is_file():
            return None
        return SkillInput(name=name, path=str(path))


async def _best_effort_interrupt(turn: Any) -> None:
    try:
        await turn.interrupt()
    except Exception as ignored:  # noqa: BLE001 - preserve original cancellation
        del ignored


def _model_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json", by_alias=True)
        return payload if isinstance(payload, dict) else {}
    if isinstance(value, dict):
        return value
    return {}


def _codex_progress(event: Any) -> str | None:
    """Summarize public SDK activity without exposing hidden reasoning."""
    method = str(getattr(event, "method", ""))
    if not method:
        return None
    if "reasoning" in method.lower() or "thought" in method.lower():
        return None
    payload = getattr(event, "payload", None)
    item = getattr(payload, "item", None)
    if item is not None:
        item = getattr(item, "root", item)
        kind = getattr(item, "type", None) or type(item).__name__
        kind_text = str(getattr(kind, "value", kind))
        if "reasoning" in kind_text.lower():
            return None
        if kind_text == "mcpToolCall":
            label = f"{getattr(item, 'server', '')}.{getattr(item, 'tool', '')}"
        elif kind_text == "agentMessage":
            label = getattr(item, "text", None) or ""
        elif kind_text == "fileChange":
            label = f"{len(getattr(item, 'changes', []))} file change(s)"
        else:
            label = (
                getattr(item, "command", None)
                or getattr(item, "name", None)
                or getattr(item, "path", None)
                or ""
            )
        label_text = str(label).replace("\n", " ")[:300]
        return f"{method} {kind_text} {label_text}".strip()
    if method in {"turn/started", "turn/completed"}:
        return method
    return None
