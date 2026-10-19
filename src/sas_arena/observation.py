from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from .hooks import HookOutcome, HookRunner
from .specs import EventSpec, SpecError
from .store import ArenaStore


@dataclass(frozen=True, slots=True)
class EventObservation:
    tick: str
    payload: dict[str, Any]
    changed: bool
    outcome: HookOutcome


class EventObserver:
    """Polls an event-supplied adapter for tick and scoreboard state."""

    def __init__(self, event: EventSpec, store: ArenaStore, runner: HookRunner) -> None:
        self.event = event
        self.store = store
        self.runner = runner

    async def poll_once(self) -> EventObservation | None:
        if self.event.observation_hook is None:
            return None
        hook_name = self.event.observation_hook
        outcome = await self.runner.run(
            hook_name,
            self.event.hooks[hook_name],
            base_dir=self.event.root,
            variables={"event_root": str(self.event.root)},
        )
        if not outcome.passed:
            self.store.record_event(
                None, "event.observation_failed", {"outcome": outcome.packet()}
            )
            raise RuntimeError(f"event observation hook failed: {hook_name}")
        payload = _last_json_object(outcome.stdout)
        tick_value = payload.get("tick")
        if not isinstance(tick_value, (str, int)) or str(tick_value) == "":
            raise SpecError("observation hook must emit a JSON object with tick")
        tick = str(tick_value)
        previous = self.store.get_event_state("tick")
        changed = previous != tick
        self.store.set_event_state("tick", tick)
        self.store.set_event_state("observation", payload)
        if "scoreboard" in payload:
            self.store.set_event_state("scoreboard", payload["scoreboard"])
        self.store.record_event(
            None,
            "event.tick_changed" if changed else "event.observed",
            {"tick": tick, "payload": payload, "outcome": outcome.packet()},
        )
        return EventObservation(tick, payload, changed, outcome)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as error:  # noqa: BLE001 - observer must stay alive
                self.store.record_event(
                    None, "event.observer_error", {"error": str(error)}
                )
            await asyncio.sleep(self.event.observation_interval_seconds)


def _last_json_object(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise SpecError("observation hook did not emit a JSON object")
