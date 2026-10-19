from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Callable

from .context import ContextBuilder
from .models import AgentResult, Task, TaskKind, TaskState
from .providers import Provider
from .promotion import PromotionManager
from .router import LatencyRouter, RoutePlan
from .scope import ScopePolicy
from .shadow import ShadowDispatcher
from .store import ArenaStore
from .traffic import advance_traffic_pipeline
from .verifier import CommandVerifier
from .workspace import WorkspaceManager


@dataclass(slots=True)
class CandidateOutcome:
    provider: str
    result: AgentResult | None
    workspace: Path | None
    error: str | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        store: ArenaStore,
        providers: dict[str, Provider],
        router: LatencyRouter,
        verifier: CommandVerifier,
        workspace_manager: WorkspaceManager,
        scope: ScopePolicy,
        schema_path: Path,
        max_parallel_candidates: int = 3,
        shadow_dispatcher: ShadowDispatcher | None = None,
        context_builder: ContextBuilder | None = None,
        service_filter: str | None = None,
        promotion_manager: PromotionManager | None = None,
    ) -> None:
        self.store = store
        self.providers = providers
        self.router = router
        self.verifier = verifier
        self.workspace_manager = workspace_manager
        self.scope = scope
        self.schema_path = schema_path
        self.max_parallel_candidates = max_parallel_candidates
        self.shadow_dispatcher = shadow_dispatcher
        self.context_builder = context_builder
        self.service_filter = service_filter
        self.promotion_manager = promotion_manager
        self._session_locks: dict[tuple[str, str, str, str], asyncio.Lock] = {}

    async def run_once(self, worker: str = "orchestrator") -> bool:
        task = self.store.lease_next(worker, service=self.service_filter)
        if task is None:
            return False
        self.store.set_state(task.id, TaskState.RUNNING)
        source = self.scope.require_workdir(task.workdir)
        if self.context_builder is not None:
            task = self.context_builder.enrich(task)
        plan = self.router.plan(
            task, {name: provider.family for name, provider in self.providers.items()}
        )
        provider_names = self._bounded_provider_order(plan)
        if not provider_names:
            self.store.requeue_or_fail(task, "no enabled provider can handle the task")
            return True

        if self.shadow_dispatcher is not None and int(
            task.context.get("shadow_generation", 0)
        ) == 0:
            deepseek_provider = next(
                (
                    name
                    for name, provider in self.providers.items()
                    if provider.family == "deepseek"
                ),
                None,
            )
            fast_provider = deepseek_provider or next(
                (
                    name
                    for name, provider in self.providers.items()
                    if provider.family == "claude"
                ),
                None,
            )
            has_codex = any(
                self.providers[name].family == "codex" for name, _ in provider_names
            )
            if fast_provider is not None and has_codex:
                if (
                    task.kind == TaskKind.REVERSE_EXPLOIT
                    and deepseek_provider is not None
                ):
                    self.shadow_dispatcher.seed_reverse_workers(
                        task, deepseek_provider
                    )
                else:
                    self.shadow_dispatcher.seed_fast_probe(task, fast_provider)

        pending = {
            asyncio.create_task(self._candidate(task, provider_name, source, delay))
            for provider_name, delay in provider_names
        }
        accepted = False
        errors: list[str] = []
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for future in done:
                    outcome = await future
                    if outcome.error:
                        errors.append(f"{outcome.provider}: {outcome.error}")
                        continue
                    assert outcome.result is not None and outcome.workspace is not None
                    self.store.set_state(task.id, TaskState.VERIFYING)
                    verification = await self.verifier.verify(
                        task, outcome.result, outcome.workspace
                    )
                    self.store.record_result(
                        task,
                        outcome.result,
                        verification,
                        accepted=verification.passed,
                    )
                    if verification.passed:
                        self.store.set_state(
                            task.id,
                            TaskState.ACCEPTED,
                            {
                                "provider": outcome.provider,
                                "latency_ms": outcome.result.latency_ms,
                            },
                        )
                        advance_traffic_pipeline(
                            self.store,
                            task,
                            outcome.result,
                            outcome.workspace,
                            provider_family=self.providers[outcome.provider].family,
                        )
                        if self.promotion_manager is not None:
                            self.promotion_manager.capture(
                                task, outcome.result, outcome.workspace
                            )
                        accepted = True
                        for other in pending:
                            other.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        pending.clear()
                        break
        finally:
            for future in pending:
                future.cancel()

        if not accepted:
            self.store.requeue_or_fail(
                task, "; ".join(errors) or "all candidates failed"
            )
        return True

    async def run_pool(
        self,
        *,
        workers: int,
        idle_exit_seconds: float = 0,
        poll_seconds: float = 0.25,
        active_workers: Callable[[], int] | None = None,
    ) -> int:
        if workers <= 0:
            raise ValueError("workers must be positive")

        processed = 0
        processed_lock = asyncio.Lock()

        async def worker_loop(index: int) -> None:
            nonlocal processed
            loop = asyncio.get_running_loop()
            idle_since = loop.time()
            while True:
                if active_workers is not None and index >= max(1, active_workers()):
                    await asyncio.sleep(poll_seconds)
                    continue
                did_work = await self.run_once(f"controller-{index}")
                if did_work:
                    async with processed_lock:
                        processed += 1
                    idle_since = loop.time()
                    continue
                if (
                    idle_exit_seconds
                    and loop.time() - idle_since >= idle_exit_seconds
                    and not self.store.has_active_tasks()
                ):
                    return
                await asyncio.sleep(poll_seconds)

        await asyncio.gather(*(worker_loop(index) for index in range(workers)))
        return processed

    async def close(self) -> None:
        await asyncio.gather(
            *(provider.close() for provider in set(self.providers.values())),
            return_exceptions=False,
        )

    def _bounded_provider_order(self, plan: RoutePlan) -> list[tuple[str, float]]:
        ordered = [(name, 0.0) for name in plan.immediate]
        ordered.extend((name, plan.hedge_delay_seconds) for name in plan.hedged)
        unique: list[tuple[str, float]] = []
        seen: set[str] = set()
        for item in ordered:
            if item[0] in self.providers and item[0] not in seen:
                seen.add(item[0])
                unique.append(item)
        return unique[: self.max_parallel_candidates]

    async def _candidate(
        self, task: Task, provider_name: str, source: Path, delay: float
    ) -> CandidateOutcome:
        try:
            if delay:
                await asyncio.sleep(delay)
            if task.context.get("captain_session"):
                role, revision = self._session_coordinates(task)
                key = (task.service, role, provider_name, revision)
                lock = self._session_locks.setdefault(key, asyncio.Lock())
                async with lock:
                    return await self._execute_candidate(task, provider_name, source)
            return await self._execute_candidate(task, provider_name, source)
        except asyncio.CancelledError:
            self.store.record_event(
                task.id, "agent.cancelled", {"provider": provider_name}
            )
            raise
        except Exception as error:  # noqa: BLE001 - one provider must not stop the race
            self.store.record_event(
                task.id,
                "agent.failed",
                {"provider": provider_name, "error": str(error)[-2000:]},
            )
            return CandidateOutcome(provider_name, None, None, str(error))

    async def _execute_candidate(
        self, task: Task, provider_name: str, source: Path
    ) -> CandidateOutcome:
        provider_task = self._with_persistent_session(task, provider_name)
        queued_messages: list[dict] = []
        if self.providers[provider_name].family != "codex":
            queued_messages = self.store.pending_agent_messages(
                task.id, provider_name
            )
            if queued_messages:
                provider_task = replace(
                    provider_task,
                    context={
                        **provider_task.context,
                        "operator_messages": [
                            str(item["message"]) for item in queued_messages
                        ],
                    },
                )
        workspace = await self.workspace_manager.prepare(
            provider_task, provider_name, source
        )
        self.store.record_event(
            task.id,
            "agent.started",
            {"provider": provider_name, "workspace": str(workspace)},
        )
        result = await self.providers[provider_name].execute(
            provider_task,
            workspace,
            self.schema_path,
            self._observe_progress,
        )
        for item in queued_messages:
            self.store.finish_agent_message(int(item["id"]))
        self.store.record_event(
            task.id,
            "agent.completed",
            {
                "provider": provider_name,
                "summary": result.summary[:1000],
                "latency_ms": result.latency_ms,
            },
        )
        if result.session_id and provider_task.context.get("captain_session"):
            role, revision = self._session_coordinates(provider_task)
            self.store.save_agent_session(
                provider_task.service,
                role,
                provider_name,
                revision,
                result.session_id,
            )
        return CandidateOutcome(provider_name, result, workspace)

    async def _observe_progress(
        self, task: Task, provider_family: str, text: str
    ) -> None:
        public_text = " ".join(text.strip().split())[:2000]
        if not public_text:
            return
        self.store.record_event(
            task.id,
            "agent.progress",
            {"provider_family": provider_family, "text": public_text},
        )
        if self.shadow_dispatcher is not None:
            await self.shadow_dispatcher.observe(task, provider_family, public_text)

    def _with_persistent_session(self, task: Task, provider_name: str) -> Task:
        if not task.context.get("captain_session"):
            return task
        role, revision = self._session_coordinates(task)
        session = self.store.get_agent_session(
            task.service, role, provider_name, revision
        )
        if session is None:
            return task
        key = f"{self.providers[provider_name].family}_session_id"
        return replace(task, context={**task.context, key: session})

    @staticmethod
    def _session_coordinates(task: Task) -> tuple[str, str]:
        role = str(task.context.get("role") or f"{task.lane.value}-captain")
        packet = task.context.get("_arena_context", {})
        freshness = packet.get("freshness", {}) if isinstance(packet, dict) else {}
        revision = str(
            task.context.get("service_revision")
            or (
                freshness.get("service_revision") if isinstance(freshness, dict) else ""
            )
            or "unknown"
        )
        return role, revision
