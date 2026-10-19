from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from .config import ArenaConfig
from .models import Risk, Task, TaskKind, TaskLane
from .specs import EventSpec
from .store import ArenaStore


class CompetitionPhase(StrEnum):
    SETUP = "setup"
    GRACE = "grace"
    LIVE = "live"
    FROZEN = "frozen"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class PhaseProfile:
    cell_workers: int
    codex_tasks_per_service: int
    shadow_children_per_codex: int
    offense_enabled: bool
    submission_enabled: bool
    monitoring_enabled: bool
    traffic_enabled: bool

    @classmethod
    def from_dict(
        cls, value: dict[str, Any], *, defaults: PhaseProfile
    ) -> PhaseProfile:
        profile = cls(
            cell_workers=int(value.get("cell_workers", defaults.cell_workers)),
            codex_tasks_per_service=int(
                value.get(
                    "codex_tasks_per_service", defaults.codex_tasks_per_service
                )
            ),
            shadow_children_per_codex=int(
                value.get(
                    "shadow_children_per_codex",
                    defaults.shadow_children_per_codex,
                )
            ),
            offense_enabled=bool(
                value.get("offense_enabled", defaults.offense_enabled)
            ),
            submission_enabled=bool(
                value.get("submission_enabled", defaults.submission_enabled)
            ),
            monitoring_enabled=bool(
                value.get("monitoring_enabled", defaults.monitoring_enabled)
            ),
            traffic_enabled=bool(
                value.get("traffic_enabled", defaults.traffic_enabled)
            ),
        )
        if not 1 <= profile.cell_workers <= 64:
            raise ValueError("phase cell_workers must be between 1 and 64")
        if not 0 <= profile.codex_tasks_per_service <= 32:
            raise ValueError(
                "phase codex_tasks_per_service must be between 0 and 32"
            )
        if not 0 <= profile.shadow_children_per_codex <= 32:
            raise ValueError(
                "phase shadow_children_per_codex must be between 0 and 32"
            )
        return profile


@dataclass(frozen=True, slots=True)
class PhasePolicy:
    grace_duration_seconds: int
    setup: PhaseProfile
    grace: PhaseProfile
    live: PhaseProfile
    frozen: PhaseProfile
    ended: PhaseProfile

    @classmethod
    def from_config(cls, config: ArenaConfig) -> PhasePolicy:
        values = config.section("phases")
        setup = PhaseProfile(2, 0, 0, False, False, True, False)
        grace = PhaseProfile(10, 5, 10, False, False, True, False)
        live = PhaseProfile(5, 1, 4, True, True, True, True)
        frozen = PhaseProfile(5, 1, 4, True, True, True, True)
        ended = PhaseProfile(1, 0, 0, False, False, False, False)
        duration = int(values.get("grace_duration_seconds", 3600))
        if duration <= 0:
            raise ValueError("phases.grace_duration_seconds must be positive")
        return cls(
            grace_duration_seconds=duration,
            setup=PhaseProfile.from_dict(values.get("setup", {}), defaults=setup),
            grace=PhaseProfile.from_dict(values.get("grace", {}), defaults=grace),
            live=PhaseProfile.from_dict(values.get("live", {}), defaults=live),
            frozen=PhaseProfile.from_dict(
                values.get("frozen", {}), defaults=frozen
            ),
            ended=PhaseProfile.from_dict(values.get("ended", {}), defaults=ended),
        )

    def profile(self, phase: CompetitionPhase | str) -> PhaseProfile:
        return getattr(self, CompetitionPhase(phase).value)


class PhaseController:
    STATE_KEY = "competition_phase"

    def __init__(
        self,
        event: EventSpec,
        store: ArenaStore,
        policy: PhasePolicy,
        *,
        service_ids: set[str] | None = None,
    ) -> None:
        self.event = event
        self.store = store
        self.policy = policy
        self.service_ids = service_ids

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        value = self.store.get_event_state(self.STATE_KEY)
        if not isinstance(value, dict):
            value = self._state(CompetitionPhase.SETUP, now)
            self.store.set_event_state(self.STATE_KEY, value)
        phase = CompetitionPhase(str(value.get("phase", CompetitionPhase.SETUP)))
        if phase in {CompetitionPhase.LIVE, CompetitionPhase.FROZEN}:
            live_started_at = float(value.get("live_started_at", now))
            tick = max(1, int((now - live_started_at) // self.event.tick_seconds) + 1)
            if tick != int(value.get("tick", 0)):
                value = {
                    **value,
                    "tick": tick,
                    "tick_started_at": live_started_at
                    + (tick - 1) * self.event.tick_seconds,
                    "next_tick_at": live_started_at + tick * self.event.tick_seconds,
                    "updated_at": now,
                }
                self.store.set_event_state(self.STATE_KEY, value)
                self.store.set_event_state("tick", str(tick))
                self.store.record_event(
                    None,
                    "phase.tick_changed",
                    {"phase": phase.value, "tick": tick},
                )
        deadline = value.get("deadline_at")
        value = dict(value)
        value["remaining_seconds"] = (
            None if deadline is None else max(0, int(float(deadline) - now))
        )
        value["grace_expired"] = bool(
            phase == CompetitionPhase.GRACE
            and deadline is not None
            and now >= float(deadline)
        )
        return value

    def start_grace(
        self,
        *,
        duration_seconds: int | None = None,
        now: float | None = None,
        seed_tasks: bool = True,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        duration = duration_seconds or self.policy.grace_duration_seconds
        if duration <= 0:
            raise ValueError("grace duration must be positive")
        previous = self.snapshot(now=now)
        if CompetitionPhase(str(previous["phase"])) != CompetitionPhase.SETUP:
            raise ValueError("grace can only start from setup phase")
        state = self._state(
            CompetitionPhase.GRACE,
            now,
            deadline_at=now + duration,
            grace_started_at=now,
        )
        self._save_transition(state)
        if seed_tasks:
            self._seed_codex_tasks(CompetitionPhase.GRACE, state)
        return self.snapshot(now=now)

    def start_live(
        self, *, now: float | None = None, seed_tasks: bool = True
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        previous = self.snapshot(now=now)
        phase = CompetitionPhase(str(previous["phase"]))
        if phase not in {CompetitionPhase.SETUP, CompetitionPhase.GRACE}:
            raise ValueError(f"cannot start live play from phase {phase.value}")
        state = self._state(
            CompetitionPhase.LIVE,
            now,
            live_started_at=now,
            tick=1,
            tick_started_at=now,
            next_tick_at=now + self.event.tick_seconds,
        )
        self._save_transition(state)
        self.store.set_event_state("tick", "1")
        if seed_tasks:
            self._seed_codex_tasks(CompetitionPhase.LIVE, state)
        return self.snapshot(now=now)

    def freeze(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        previous = self.snapshot(now=now)
        if CompetitionPhase(str(previous["phase"])) != CompetitionPhase.LIVE:
            raise ValueError("scoreboard freeze requires live phase")
        state = {
            **previous,
            "phase": CompetitionPhase.FROZEN.value,
            "profile": asdict(self.policy.frozen),
            "started_at": now,
            "updated_at": now,
            "deadline_at": None,
        }
        state.pop("remaining_seconds", None)
        state.pop("grace_expired", None)
        self._save_transition(state)
        return self.snapshot(now=now)

    def end(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        state = self._state(CompetitionPhase.ENDED, now)
        self._save_transition(state)
        return self.snapshot(now=now)

    def apply_remote(self, state: dict[str, Any], *, seed_tasks: bool = True) -> None:
        phase = CompetitionPhase(str(state.get("phase", "")))
        if str(state.get("event", "")) != self.event.id:
            raise ValueError("remote phase state belongs to a different event")
        previous = self.store.get_event_state(self.STATE_KEY)
        changed = not isinstance(previous, dict) or (
            previous.get("phase"), previous.get("started_at")
        ) != (state.get("phase"), state.get("started_at"))
        persisted = {
            key: value
            for key, value in state.items()
            if key not in {"remaining_seconds", "grace_expired"}
        }
        self.store.set_event_state(self.STATE_KEY, persisted)
        if int(persisted.get("tick", 0)) > 0:
            self.store.set_event_state("tick", str(persisted["tick"]))
        if changed:
            self.store.record_event(
                None,
                "phase.remote_transition",
                {"state": persisted},
            )
            if seed_tasks and phase in {CompetitionPhase.GRACE, CompetitionPhase.LIVE}:
                self._seed_codex_tasks(phase, persisted)

    def _state(
        self,
        phase: CompetitionPhase,
        now: float,
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "phase": phase.value,
            "event": self.event.id,
            "started_at": now,
            "updated_at": now,
            "deadline_at": None,
            "tick": 0,
            "tick_seconds": self.event.tick_seconds,
            "profile": asdict(self.policy.profile(phase)),
            **extra,
        }

    def _save_transition(self, state: dict[str, Any]) -> None:
        previous = self.store.get_event_state(self.STATE_KEY)
        self.store.set_event_state(self.STATE_KEY, state)
        self.store.record_event(
            None,
            "phase.transition",
            {
                "from": previous.get("phase") if isinstance(previous, dict) else None,
                "to": state["phase"],
                "state": state,
            },
        )

    def _seed_codex_tasks(
        self, phase: CompetitionPhase, state: dict[str, Any]
    ) -> None:
        profile = self.policy.profile(phase)
        if profile.codex_tasks_per_service <= 0:
            return
        started = int(float(state["started_at"]))
        for service in self.event.services.values():
            if self.service_ids is not None and service.id not in self.service_ids:
                continue
            goals = (
                _grace_goals(service.name)
                if phase == CompetitionPhase.GRACE
                else _live_goals(service.name)
            )
            for index in range(profile.codex_tasks_per_service):
                goal = goals[index % len(goals)]
                captain = index == 0
                task = Task(
                    service=service.id,
                    kind=TaskKind.DIAGNOSE if captain else TaskKind.VULN_SCOUT,
                    lane=(
                        TaskLane.ANALYSIS
                        if phase == CompetitionPhase.GRACE
                        else TaskLane.OPERATIONS
                    ),
                    goal=goal,
                    workdir=str(service.root),
                    risk=Risk.HIGH,
                    expected_value=0.95 if captain else 0.8,
                    deadline_seconds=(
                        max(600, self.policy.grace_duration_seconds)
                        if phase == CompetitionPhase.GRACE
                        else max(300, int(self.event.tick_seconds * 4))
                    ),
                    context={
                        "phase": phase.value,
                        "role": (
                            "service-captain"
                            if captain
                            else f"{phase.value}-codex-specialist-{index}"
                        ),
                        "operator_mode": captain,
                        "provider_fallbacks": ["codex"] if captain else [],
                        "captain_session": captain,
                        "write_access": True,
                        "service_revision": str(
                            service.context.get("revision", "unknown")
                        ),
                        "shadow_max_children": profile.shadow_children_per_codex,
                        "technical_objective": goal,
                        "verification": {"commands": []},
                    },
                    preferred_provider="claude" if captain else "codex",
                    dedupe_key=(
                        f"phase:{self.event.id}:{phase.value}:{started}:"
                        f"{service.id}:codex:{index}"
                    ),
                )
                self.store.enqueue(task)


def current_profile(store: ArenaStore) -> dict[str, Any]:
    state = store.get_event_state(PhaseController.STATE_KEY)
    if not isinstance(state, dict) or not isinstance(state.get("profile"), dict):
        return {}
    return dict(state["profile"])


def phase_allows(store: ArenaStore, capability: str, *, default: bool) -> bool:
    profile = current_profile(store)
    return bool(profile.get(capability, default))


def _grace_goals(service: str) -> tuple[str, ...]:
    return (
        f"Lead the grace-period audit of {service}: map the checker, flag stores, trust boundaries, and coordinate parallel exploit and patch hypotheses.",
        f"Audit {service} for the fastest high-confidence exploit path and build a local synthetic-flag proof plan.",
        f"Analyze {service} authentication, authorization, sessions, and business logic for independent exploit paths.",
        f"Analyze {service} protocol, parsing, crypto, concurrency, and native boundaries where present.",
        f"Review checker-preserving defense options for {service}; identify patches that can be prepared safely before live play.",
        f"Build an alternative exploit hypothesis for {service} and challenge the strongest current assumptions with concrete tests.",
    )


def _live_goals(service: str) -> tuple[str, ...]:
    return (
        f"Act as live service captain for {service}: prioritize verified exploit yield, checker health, incidents, and the smallest safe patches.",
        f"Investigate the highest-value unresolved live issue for {service} using current traffic, checker, and exploit evidence.",
    )
