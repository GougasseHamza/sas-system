from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .models import Risk, Task, TaskKind
from .store import ArenaStore

MARKER = "DISPATCH_HINT:"


@dataclass(frozen=True, slots=True)
class DispatchHint:
    kind: TaskKind
    goal: str
    evidence: tuple[dict[str, Any], ...]
    priority: float = 0.5
    write_access: bool = False


def parse_dispatch_hints(text: str) -> list[DispatchHint]:
    hints: list[DispatchHint] = []
    decoder = json.JSONDecoder()
    offset = 0
    while True:
        marker_at = text.find(MARKER, offset)
        if marker_at < 0:
            break
        start = marker_at + len(MARKER)
        while start < len(text) and text[start].isspace():
            start += 1
        try:
            payload, consumed = decoder.raw_decode(text[start:])
            offset = start + consumed
            hint = _hint_from_payload(payload)
            if hint is not None:
                hints.append(hint)
        except (json.JSONDecodeError, TypeError, ValueError):
            offset = start + 1
    return hints


def _hint_from_payload(payload: Any) -> DispatchHint | None:
    if not isinstance(payload, dict):
        return None
    goal = payload.get("goal")
    if not isinstance(goal, str) or not 8 <= len(goal.strip()) <= 1000:
        return None
    kind = TaskKind(payload.get("kind", TaskKind.VULN_SCOUT.value))
    evidence = payload.get("evidence", [])
    if not isinstance(evidence, list) or not all(
        isinstance(item, dict) for item in evidence
    ):
        return None
    priority = float(payload.get("priority", 0.5))
    if not 0 <= priority <= 1:
        return None
    return DispatchHint(
        kind=kind,
        goal=goal.strip(),
        evidence=tuple(evidence),
        priority=priority,
        write_access=bool(payload.get("write_access", False)),
    )


class ShadowDispatcher:
    """Converts explicit Codex checkpoints into bounded, deduplicated child tasks."""

    def __init__(
        self,
        store: ArenaStore,
        *,
        max_children_per_parent: int = 8,
        default_deadline_seconds: int = 180,
    ) -> None:
        self.store = store
        self.max_children_per_parent = max_children_per_parent
        self.default_deadline_seconds = default_deadline_seconds

    async def observe(self, parent: Task, provider: str, text: str) -> None:
        if provider != "codex" or int(parent.context.get("shadow_generation", 0)) > 0:
            return
        remaining = self._limit(parent) - self.store.count_children(parent.id)
        if remaining <= 0:
            return
        for hint in parse_dispatch_hints(text)[:remaining]:
            self.dispatch(parent, hint)

    def dispatch(self, parent: Task, hint: DispatchHint) -> tuple[str, bool]:
        """Queue one bounded child task, enforcing the per-parent fan-out cap."""
        if int(parent.context.get("shadow_generation", 0)) > 0:
            raise ValueError("shadow children cannot recursively dispatch")
        if self.store.count_children(parent.id) >= self._limit(parent):
            raise ValueError("parent has reached its shadow child limit")
        child_id, created = self.store.enqueue(self._child(parent, hint))
        self.store.record_event(
            parent.id,
            "shadow.dispatched",
            {"child_task_id": child_id, "created": created, "kind": hint.kind.value},
        )
        return child_id, created

    def seed_fast_probe(self, parent: Task, provider: str) -> tuple[str, bool]:
        """Start useful parallel work before Codex emits its first checkpoint."""
        objective = str(parent.context.get("technical_objective", parent.goal))
        implementation = parent.kind in {
            TaskKind.EXPLOIT_BUILD,
            TaskKind.REVERSE_EXPLOIT,
            TaskKind.PATCH_BUILD,
        }
        child_kind = (
            TaskKind.EXPLOIT_BUILD
            if parent.kind == TaskKind.REVERSE_EXPLOIT
            else parent.kind if implementation else TaskKind.VULN_SCOUT
        )
        hint = DispatchHint(
            kind=child_kind,
            goal=(
                "Work independently; do not call dispatch or worker-update tools. "
                + (
                    "Implement a minimal candidate in the supplied isolated CTF "
                    "workspace and report the exact files changed. "
                    if implementation
                    else "Review only the supplied local CTF source and validate one "
                    "bounded hypothesis with file/line evidence and a synthetic local "
                    "test plan. "
                )
                + f"Technical objective: {objective}"
            )[:1000],
            evidence=(),
            priority=max(0.65, parent.expected_value - 0.1),
            write_access=implementation and bool(parent.context.get("write_access")),
        )
        child = self._child(parent, hint)
        if implementation:
            child.context["verification"] = parent.context.get(
                "verification", {"commands": []}
            )
        child.preferred_provider = provider
        child.dedupe_key = f"shadow-prefetch:{parent.id}:{provider}"
        child_id, created = self.store.enqueue(child)
        self.store.record_event(
            parent.id,
            "shadow.prefetched",
            {"child_task_id": child_id, "created": created, "provider": provider},
        )
        return child_id, created

    def seed_reverse_workers(
        self, parent: Task, provider: str
    ) -> list[tuple[str, bool]]:
        """Start complementary DeepSeek branches beside a Codex reverse captain."""

        if parent.kind != TaskKind.REVERSE_EXPLOIT:
            raise ValueError("reverse workers require a reverse_exploit parent")
        branches = (
            DispatchHint(
                TaskKind.VULN_SCOUT,
                "Map the captured protocol and state transitions against the checker; "
                "report exact source and replay evidence without editing files.",
                (),
                0.85,
                False,
            ),
            DispatchHint(
                TaskKind.VULN_SCOUT,
                "Trace the reconstructed request to its security-sensitive source, "
                "sink, authorization checks, and likely patch point.",
                (),
                0.85,
                False,
            ),
            DispatchHint(
                TaskKind.EXPLOIT_BUILD,
                "Independently turn the proven local replay into a robust exploit "
                "candidate and preserve concrete verification evidence.",
                (),
                0.9,
                True,
            ),
        )
        created: list[tuple[str, bool]] = []
        for index, hint in enumerate(branches):
            if self.store.count_children(parent.id) >= self._limit(parent):
                break
            child = self._child(parent, hint)
            if hint.kind == TaskKind.EXPLOIT_BUILD:
                child.context["verification"] = parent.context.get(
                    "verification", {"commands": []}
                )
            child.preferred_provider = provider
            child.dedupe_key = f"shadow-reverse:{parent.id}:{index}:{provider}"
            child_id, was_created = self.store.enqueue(child)
            self.store.record_event(
                parent.id,
                "shadow.prefetched",
                {
                    "child_task_id": child_id,
                    "created": was_created,
                    "provider": provider,
                    "branch": index,
                    "kind": hint.kind.value,
                },
            )
            created.append((child_id, was_created))
        return created

    def _limit(self, parent: Task) -> int:
        requested = int(
            parent.context.get("shadow_max_children", self.max_children_per_parent)
        )
        return max(0, min(requested, self.max_children_per_parent))

    def _child(self, parent: Task, hint: DispatchHint) -> Task:
        risk = (
            Risk.HIGH
            if hint.kind
            in {
                TaskKind.EXPLOIT_BUILD,
                TaskKind.REVERSE_EXPLOIT,
                TaskKind.PATCH_BUILD,
                TaskKind.DIAGNOSE,
            }
            else Risk.NORMAL
        )
        context = {
            "write_access": hint.write_access,
            "shadow_generation": 1,
            "codex_checkpoint_evidence": list(hint.evidence),
            "verification": {"commands": []},
        }
        return Task(
            service=parent.service,
            kind=hint.kind,
            lane=parent.lane,
            goal=hint.goal,
            workdir=parent.workdir,
            risk=risk,
            expected_value=hint.priority,
            deadline_seconds=min(
                parent.deadline_seconds, self.default_deadline_seconds
            ),
            max_attempts=2,
            context=context,
            parent_id=parent.id,
            dedupe_key=(
                f"shadow:{parent.id}:{hint.kind.value}:{hint.goal.casefold().strip()}"
            ),
        )
