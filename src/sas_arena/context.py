from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .models import Task
from .specs import EventSpec
from .store import ArenaStore


class ContextBuilder:
    """Builds bounded service packets; model transcripts are never shared wholesale."""

    def __init__(
        self,
        event: EventSpec,
        store: ArenaStore,
        *,
        max_claims: int = 20,
        max_events: int = 30,
        max_chars: int = 32_000,
    ) -> None:
        self.event = event
        self.store = store
        self.max_claims = max_claims
        self.max_events = max_events
        self.max_chars = max_chars

    def enrich(self, task: Task) -> Task:
        service = self.event.service(task.service)
        original = dict(task.context)
        observations = list(original.pop("untrusted_observations", []))
        packet = {
            "event": {
                "id": self.event.id,
                "name": self.event.name,
                "tick_seconds": self.event.tick_seconds,
                "operational_state": self.store.event_state_snapshot(),
            },
            "service": service.public_context(),
            "task_context": _without_private_runtime(original),
            "confirmed_claims": self.store.latest_claims(
                service.id, limit=self.max_claims
            ),
            "recent_events": self.store.recent_service_events(
                service.id, limit=self.max_events
            ),
            "untrusted_observations": observations,
            "freshness": {
                "service_revision": str(
                    original.get(
                        "service_revision", service.context.get("revision", "unknown")
                    )
                ),
                "note": "Revalidate evidence when its revision or deployment differs.",
            },
        }
        packet = _fit_packet(packet, self.max_chars)
        context = dict(task.context)
        context["_arena_context"] = packet
        return replace(task, context=context)


def _without_private_runtime(value: dict[str, Any]) -> dict[str, Any]:
    hidden = {
        "_arena_context",
        "codex_session_id",
        "claude_session_id",
        "verification",
    }
    return {key: item for key, item in value.items() if key not in hidden}


def _fit_packet(packet: dict[str, Any], max_chars: int) -> dict[str, Any]:
    if max_chars < 2_000:
        raise ValueError("max_chars must be at least 2000")
    mutable = dict(packet)
    for key in ("recent_events", "confirmed_claims", "untrusted_observations"):
        values = list(mutable.get(key, []))
        while values and len(json.dumps(mutable, default=str)) > max_chars:
            values.pop()
            mutable[key] = values
    if len(json.dumps(mutable, default=str)) <= max_chars:
        return mutable
    mutable["task_context"] = _truncate_strings(mutable.get("task_context", {}), 1000)
    mutable["service"] = _truncate_strings(mutable.get("service", {}), 1000)
    mutable["event"] = _truncate_strings(mutable.get("event", {}), 1000)
    return mutable


def _truncate_strings(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…[truncated]"
    if isinstance(value, list):
        return [_truncate_strings(item, limit) for item in value[:50]]
    if isinstance(value, dict):
        return {
            str(key): _truncate_strings(item, limit)
            for key, item in list(value.items())[:100]
        }
    return value
