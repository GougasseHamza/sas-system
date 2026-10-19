from __future__ import annotations

import curses
import json
import time
from typing import Any

from .cluster import NodeAssignment
from .phases import CompetitionPhase, PhaseController
from .store import ArenaStore


def dashboard_lines(
    controller: PhaseController,
    store: ArenaStore,
    *,
    node: NodeAssignment | None = None,
    now: float | None = None,
) -> list[str]:
    phase = controller.snapshot(now=now)
    profile = phase.get("profile", {})
    services = list(node.services) if node is not None else sorted(controller.event.services)
    lines = [
        "SAS Agent Arena",
        (
            f"phase={str(phase['phase']).upper()}  tick={phase.get('tick', 0)}  "
            f"remaining={_duration(phase.get('remaining_seconds'))}"
        ),
        (
            f"workers/cell={profile.get('cell_workers', '?')}  "
            f"captain+deep/service={profile.get('codex_tasks_per_service', '?')}  "
            f"children/codex={profile.get('shadow_children_per_codex', '?')}"
        ),
        (
            f"node={node.id if node else 'controller'}  "
            f"services={', '.join(services) if services else '(none)'}"
        ),
        "",
        "service              tasks(q/r/a/f)  alerts actions flags(new/retry/ok)",
    ]
    for service_id in services:
        snapshot = store.service_snapshot(service_id)
        tasks = snapshot["tasks"]
        alerts = sum(int(item["alert_open"]) for item in snapshot["monitors"])
        actions = len(
            store.list_operator_actions(service_id, status="requested", limit=200)
        )
        flags = snapshot["flags"]
        lines.append(
            f"{service_id[:20]:20} "
            f"{tasks.get('queued', 0):2}/{tasks.get('running', 0):2}/"
            f"{tasks.get('accepted', 0):2}/{tasks.get('failed', 0):2}       "
            f"{alerts:2}      {actions:2}      "
            f"{flags.get('new', 0):2}/{flags.get('retry', 0):2}/"
            f"{flags.get('accepted', 0):2}"
        )
    heartbeats = store.node_heartbeats()
    if heartbeats:
        current = time.time() if now is None else now
        lines.extend(["", "laptop heartbeat age"])
        for heartbeat in heartbeats:
            age = max(0, int(current - float(heartbeat["updated_at"])))
            lines.append(
                f"{heartbeat['node'][:20]:20} {age:4}s  "
                f"{', '.join(heartbeat['services'])}"
            )
    lines.extend(
        [
            "",
            "keys: g start grace | l start live | f freeze | e end | q quit",
            "Grace never changes to live automatically; the operator starts live play.",
        ]
    )
    if phase.get("grace_expired"):
        lines.append("GRACE TIMER EXPIRED — press l only when organizers open the network")
    return lines


def run_dashboard(
    controller: PhaseController,
    store: ArenaStore,
    *,
    node: NodeAssignment | None = None,
    refresh_seconds: float = 0.5,
    read_only: bool = False,
) -> None:
    def _main(screen: Any) -> None:
        curses.curs_set(0)
        screen.nodelay(True)
        screen.timeout(max(50, int(refresh_seconds * 1000)))
        message = ""
        while True:
            screen.erase()
            lines = dashboard_lines(controller, store, node=node)
            height, width = screen.getmaxyx()
            for row, line in enumerate(lines[: max(0, height - 2)]):
                try:
                    screen.addnstr(row, 0, line, max(1, width - 1))
                except curses.error:
                    pass
            if message and height > 1:
                try:
                    screen.addnstr(height - 1, 0, message, max(1, width - 1))
                except curses.error:
                    pass
            screen.refresh()
            key = screen.getch()
            if key in {ord("q"), 27}:
                return
            if key < 0:
                continue
            if read_only:
                message = "read-only dashboard"
                continue
            try:
                if key == ord("g"):
                    controller.start_grace()
                    message = "grace started and Codex grace tasks queued"
                elif key == ord("l"):
                    controller.start_live()
                    message = "live phase started at tick 1"
                elif key == ord("f"):
                    controller.freeze()
                    message = "scoreboard freeze recorded; live work continues"
                elif key == ord("e"):
                    controller.end()
                    message = "competition ended"
            except Exception as error:  # noqa: BLE001 - show operator input error
                message = str(error)

    curses.wrapper(_main)


def phase_json(controller: PhaseController) -> str:
    return json.dumps(controller.snapshot(), indent=2, sort_keys=True)


def _duration(value: object) -> str:
    if value is None:
        return "--:--:--"
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"
