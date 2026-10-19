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
    view: str = "overview",
    selected: int = 0,
    show_history: bool = False,
) -> list[str]:
    phase = controller.snapshot(now=now)
    profile = phase.get("profile", {})
    services = list(node.services) if node is not None else sorted(controller.event.services)
    lines = [
        "┏━ SAS AGENT ARENA  //  CONTROL PLANE",
        (
            f"┃ phase={str(phase['phase']).upper():7}  "
            f"tick={int(phase.get('tick', 0)):04}  "
            f"remaining={_duration(phase.get('remaining_seconds'))}"
        ),
        (
            f"┃ workers/cell={profile.get('cell_workers', '?')}  "
            f"captain+deep/service={profile.get('codex_tasks_per_service', '?')}  "
            f"children/codex={profile.get('shadow_children_per_codex', '?')}"
        ),
        (
            f"┃ node={node.id if node else 'controller'}  "
            f"services={', '.join(services) if services else '(none)'}"
        ),
        "┣━ SERVICE CELLS",
        "┃ service              tasks  q / r / ok / fail   alerts  actions   flags new/retry/ok",
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
            f"┃ {service_id[:20]:20}       "
            f"{tasks.get('queued', 0):2} / {tasks.get('running', 0):2} / "
            f"{tasks.get('accepted', 0):2} / {tasks.get('failed', 0):2}       "
            f"{alerts:2}       {actions:2}       "
            f"{flags.get('new', 0):2}/{flags.get('retry', 0):2}/"
            f"{flags.get('accepted', 0):2}"
        )
    activity = store.task_activity(active_only=not show_history, limit=100)
    states: dict[str, int] = {}
    for item in activity:
        states[str(item["state"])] = states.get(str(item["state"]), 0) + 1
    lines.append(
        "┃ agents=" + ", ".join(
            f"{name}:{states.get(name, 0)}"
            for name in ("queued", "running", "verifying", "accepted", "failed")
        )
    )
    if view == "agents":
        mode = "ALL TASK HISTORY" if show_history else "LIVE AGENTS"
        lines.extend([f"┣━ {mode}  (j/k select, m message, h history, o overview)",
                      "┃ state       provider   role                  kind              task"])
        if not activity:
            lines.append("┃ (no tasks; start the defense launcher or check provider readiness)")
        for index, item in enumerate(activity[:18]):
            marker = ">" if index == selected else " "
            lines.append(
                f"┃{marker}{str(item['state']):10} {str(item.get('provider') or '?'):9} "
                f"{str(item.get('role') or '-'):21.21} {str(item['kind']):17.17} "
                f"{str(item['task_id'])[:8]}"
            )
        if activity:
            item = activity[min(selected, len(activity) - 1)]
            lines.extend(["┣━ SELECTED AGENT", f"┃ id={item['task_id']}",
                          f"┃ goal={str(item['goal'])[:120]}",
                          "┃ public events:"])
            events = store.recent_task_events(item["task_id"], limit=8)
            if not events:
                lines.append("┃ (no public events yet; worker may still be starting)")
            for event in events:
                payload = event.get("payload", {})
                text = payload.get("text") or payload.get("summary") or payload.get("error") or ""
                lines.append(f"┃ {event['type']}: {str(text)[:110]}")
    heartbeats = store.node_heartbeats()
    if heartbeats:
        current = time.time() if now is None else now
        lines.extend(["┣━ LAPTOP HEARTBEATS", "┃ node                  age   services"])
        for heartbeat in heartbeats:
            age = max(0, int(current - float(heartbeat["updated_at"])))
            lines.append(
                f"┃ {heartbeat['node'][:20]:20} {age:4}s  "
                f"{', '.join(heartbeat['services'])}"
            )
    lines.extend(
        [
            "┣━ CONTROLS",
            "┃ [a] AGENTS  [j/k] SELECT  [m] MESSAGE  [h] HISTORY  [o] OVERVIEW  [q] QUIT",
            "┗━ Grace never changes to live automatically. Operator confirmation is required.",
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
    view = "overview"
    selected = 0
    show_history = False

    def _main(screen: Any) -> None:
        nonlocal view, selected, show_history
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        screen.nodelay(True)
        screen.timeout(max(50, int(refresh_seconds * 1000)))
        colors = _init_colors()
        message = ""
        while True:
            screen.erase()
            activity = store.task_activity(active_only=not show_history, limit=100)
            if activity:
                selected_index = min(selected, len(activity) - 1)
            else:
                selected_index = 0
            lines = dashboard_lines(
                controller, store, node=node, view=view, selected=selected_index,
                show_history=show_history
            )
            height, width = screen.getmaxyx()
            for row, line in enumerate(lines[: max(0, height - 2)]):
                try:
                    screen.addnstr(
                        row,
                        0,
                        line,
                        max(1, width - 1),
                        _line_attribute(line, row, colors),
                    )
                except curses.error:
                    pass
            if message and height > 1:
                try:
                    screen.addnstr(
                        height - 1,
                        0,
                        f" {message}",
                        max(1, width - 1),
                        colors["warning"] | curses.A_BOLD,
                    )
                except curses.error:
                    pass
            screen.refresh()
            key = screen.getch()
            if key in {ord("q"), 27}:
                return
            if key < 0:
                continue
            if key == ord("a"):
                view = "agents"
                message = "agent view: j/k select, m sends a provider-routed message"
                continue
            if key == ord("o"):
                view = "overview"
                continue
            if view == "agents" and key == ord("h"):
                show_history = not show_history
                selected = 0
                message = "showing all task history" if show_history else "showing active agents"
                continue
            if view == "agents" and key in {ord("j"), curses.KEY_DOWN}:
                selected = min(selected + 1, max(0, len(activity) - 1))
                continue
            if view == "agents" and key in {ord("k"), curses.KEY_UP}:
                selected = max(0, selected - 1)
                continue
            if view == "agents" and key == ord("m"):
                if not activity:
                    message = "no agent task exists"
                    continue
                item = activity[min(selected, len(activity) - 1)]
                if item["state"] in {"accepted", "rejected", "failed"}:
                    message = "selected task is terminal"
                    continue
                try:
                    curses.echo()
                    screen.addstr(screen.getmaxyx()[0] - 1, 0, " message> ")
                    text = screen.getstr(screen.getmaxyx()[0] - 1, 10, 240).decode(errors="replace").strip()
                    curses.noecho()
                    if text:
                        provider = str(item.get("provider") or "codex")
                        store.queue_agent_message(item["task_id"], provider, text)
                        message = f"queued for {provider}: {text[:70]}"
                    else:
                        message = "empty message ignored"
                except Exception as error:  # noqa: BLE001
                    curses.noecho()
                    message = str(error)
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


def _init_colors() -> dict[str, int]:
    values = {name: curses.A_NORMAL for name in (
        "header", "good", "warning", "critical", "accent", "muted"
    )}
    if not curses.has_colors():
        return values
    try:
        curses.start_color()
        curses.use_default_colors()
        for index, (name, foreground) in enumerate(
            (
                ("header", curses.COLOR_CYAN),
                ("good", curses.COLOR_GREEN),
                ("warning", curses.COLOR_YELLOW),
                ("critical", curses.COLOR_RED),
                ("accent", curses.COLOR_MAGENTA),
                ("muted", curses.COLOR_BLUE),
            ),
            start=1,
        ):
            curses.init_pair(index, foreground, -1)
            values[name] = curses.color_pair(index)
    except curses.error:
        pass
    return values


def _line_attribute(line: str, row: int, colors: dict[str, int]) -> int:
    if row == 0:
        return colors["header"] | curses.A_BOLD
    if "phase=LIVE" in line:
        return colors["good"] | curses.A_BOLD
    if "phase=GRACE" in line:
        return colors["warning"] | curses.A_BOLD
    if "phase=FROZEN" in line:
        return colors["accent"] | curses.A_BOLD
    if "phase=ENDED" in line or "EXPIRED" in line:
        return colors["critical"] | curses.A_BOLD
    if "phase=SETUP" in line:
        return colors["header"] | curses.A_BOLD
    if line.startswith("┣━") or line.startswith("┗━"):
        return colors["header"] | curses.A_BOLD
    if line.startswith("┃ service") or line.startswith("┃ node"):
        return colors["accent"] | curses.A_BOLD
    if line.startswith("┃ ") and "/" in line:
        return colors["good"]
    if line.startswith("┃ ["):
        return colors["warning"] | curses.A_BOLD
    return colors["muted"]
