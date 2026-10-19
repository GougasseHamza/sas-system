from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from .models import Risk, Task, TaskKind


@dataclass(frozen=True, slots=True)
class RoutePlan:
    immediate: tuple[str, ...]
    hedged: tuple[str, ...] = ()
    hedge_delay_seconds: float = 90.0


class LatencyRouter:
    """Selects parallel candidates; verification, not model confidence, picks the winner."""

    def __init__(self, codex_hedge_delay_seconds: float = 90.0) -> None:
        self.codex_hedge_delay_seconds = codex_hedge_delay_seconds

    def plan(self, task: Task, available: set[str] | Mapping[str, str]) -> RoutePlan:
        families = (
            {name: name for name in available}
            if isinstance(available, set)
            else dict(available)
        )
        if task.preferred_provider:
            if task.preferred_provider in families:
                return RoutePlan((task.preferred_provider,))
            matches = sorted(
                name
                for name, family in families.items()
                if family == task.preferred_provider
            )
            if matches:
                return RoutePlan(tuple(matches[:1]))
            fallbacks = task.context.get("provider_fallbacks", [])
            if isinstance(fallbacks, list):
                for fallback in fallbacks:
                    fallback_name = str(fallback)
                    if fallback_name in families:
                        return RoutePlan((fallback_name,))
                    fallback_match = _select_family(
                        families, fallback_name, task.id
                    )
                    if fallback_match is not None:
                        return RoutePlan((fallback_match,))
            return RoutePlan(())

        fast_order = tuple(
            name
            for family in ("deepseek", "claude")
            for name in [_select_family(families, family, task.id)]
            if name is not None
        )
        captain_key = ":".join(
            (
                task.service,
                task.lane.value,
                str(task.context.get("role", "captain")),
            )
        )
        codex_name = _select_family(families, "codex", captain_key)
        codex = (codex_name,) if codex_name is not None else ()

        hard_kind = task.kind in {
            TaskKind.EXPLOIT_BUILD,
            TaskKind.EXPLOIT_VERIFY,
            TaskKind.TRAFFIC_RECONSTRUCT,
            TaskKind.REVERSE_EXPLOIT,
            TaskKind.PATCH_BUILD,
            TaskKind.PATCH_VERIFY,
            TaskKind.INCIDENT_RESPONSE,
            TaskKind.DIAGNOSE,
        }
        high_value = task.expected_value >= 0.75

        if task.risk == Risk.CRITICAL or (task.risk == Risk.HIGH and high_value):
            # Codex starts immediately, but never serializes the fast attempts.
            return RoutePlan(fast_order + codex)

        if hard_kind or high_value:
            if fast_order:
                return RoutePlan(
                    fast_order,
                    codex,
                    0.0 if task.attempts > 1 else self.codex_hedge_delay_seconds,
                )
            return RoutePlan(codex)

        if fast_order:
            # Routine mapping stays fast unless it already failed once.
            if task.attempts > 1:
                return RoutePlan(fast_order, codex, 0.0)
            return RoutePlan(fast_order)
        return RoutePlan(codex)


def _select_family(
    families: Mapping[str, str], family: str, routing_key: str
) -> str | None:
    candidates = [name for name in sorted(families) if families[name] == family]
    if not candidates:
        return None
    digest = hashlib.sha256(routing_key.encode()).digest()
    return candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
