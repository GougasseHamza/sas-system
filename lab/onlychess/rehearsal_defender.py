#!/usr/bin/env python3
"""Launch the blue-team monitor, traffic pipeline, workers, and dashboard."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sas_arena.config import ArenaConfig
from sas_arena.models import Risk, Task, TaskKind, TaskLane
from sas_arena.specs import EventSpec
from sas_arena.store import ArenaStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG = "config/arena.onlychess-remote.toml"
EVENT = "events/sas2025-onlychess-remote/event.toml"


def arena(*arguments: str) -> list[str]:
    return ["uv", "run", "--no-sync", "sas-arena", "--config", CONFIG, *arguments]


def checked(*arguments: str) -> None:
    subprocess.run(arena(*arguments), cwd=PROJECT_ROOT, check=True)


def ensure_live_phase() -> None:
    result = subprocess.run(
        arena("phase-status", EVENT),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    phase = str(json.loads(result.stdout).get("phase", ""))
    if phase in {"setup", "grace"}:
        checked("phase-live", EVENT)
        return
    if phase not in {"live", "frozen"}:
        raise RuntimeError(f"cannot launch defender in phase {phase!r}")


def seed_defense_team() -> None:
    """Queue the visible blue-team starter roles before worker processes start."""
    config = ArenaConfig.load(PROJECT_ROOT / CONFIG)
    database = config.section("state")["database"]
    store = ArenaStore(config.project_root / database)
    store.initialize()
    service = EventSpec.load(PROJECT_ROOT / EVENT).service("onlychess")
    revision = str(service.context.get("revision", "unknown"))
    starters = (
        (TaskKind.TRAFFIC_TRIAGE, "claude", "traffic-analyst", True, False,
         "Review the service traffic/checker boundary and stand by for suspicious traffic. Prepare bounded detection and triage notes; do not invent observations."),
        (TaskKind.TRAFFIC_RECONSTRUCT, "claude", "traffic-reconstructor", True, False,
         "Prepare the exact replay workflow for suspicious requests and identify the evidence needed before escalating to reverse exploitation."),
        (TaskKind.REVERSE_EXPLOIT, "codex", "reverse-exploit-captain", True, True,
         "Review service source and checker behavior for defensive reverse-exploitation readiness. When traffic evidence arrives, reconstruct the exploit and identify the smallest root-cause patch."),
        (TaskKind.PATCH_BUILD, "codex", "patch-captain", True, True,
         "Audit the service trust boundaries and prepare a minimal checker-preserving patch strategy. Wait for concrete incident evidence before changing code."),
        (TaskKind.PATCH_BUILD, "claude", "patch-builder", True, True,
         "Independently map likely patch locations and regression cases. Produce a candidate only from concrete monitor, checker, source, or traffic evidence."),
        (TaskKind.PATCH_VERIFY, "deepseek", "patch-verifier", True, False,
         "Act as an independent defense reviewer. Check candidate patches for exploit blocking, checker compatibility, and availability; report exact failing tests."),
    )
    for kind, provider, role, captain, write_access, goal in starters:
        task_id, created = store.enqueue(Task(
            service=service.id,
            kind=kind,
            lane=TaskLane.DEFENSE,
            risk=Risk.HIGH if kind in {TaskKind.REVERSE_EXPLOIT, TaskKind.PATCH_BUILD} else Risk.NORMAL,
            expected_value=0.9,
            deadline_seconds=1800,
            preferred_provider=provider,
            goal=goal,
            workdir=str(service.root),
            context={
                "role": role,
                "captain_session": captain,
                "operator_mode": True,
                "write_access": write_access,
                "service_revision": revision,
                "verification": {"commands": []},
                "defense_starter": True,
            },
            dedupe_key=f"defense-starter:{service.id}:{role}:{provider}",
        ))
        if not created:
            store.retry_terminal_task(task_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--no-tui", action="store_true")
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    os.environ.setdefault("SAS_TEAM_TOKEN", "rehearsal-team-token")

    checked("doctor")
    checked("event-validate", EVENT)
    seed_defense_team()
    ensure_live_phase()

    log_root = PROJECT_ROOT / ".arena" / "rehearsal-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    specifications = {
        "monitor": arena("monitor", EVENT),
        "traffic": arena("traffic", EVENT),
        "observer": arena("observe", EVENT),
        "workers": arena("--event", EVENT, "run", "--workers", str(args.workers)),
    }
    children: list[tuple[str, subprocess.Popen, object]] = []
    try:
        for name, command in specifications.items():
            log = (log_root / f"{name}.log").open("a")
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            children.append((name, process, log))
            print(f"started {name} pid={process.pid} log={log.name}")
        print(
            "Blue team is live. Monitor and traffic preflight now run in the "
            "background so the TUI opens immediately. Ctrl-C stops all four "
            "background processes."
        )
        if not args.no_tui:
            command = arena("tui", EVENT)
            if args.read_only:
                command.append("--read-only")
            return subprocess.run(command, cwd=PROJECT_ROOT).returncode
        while True:
            for name, process, _ in children:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited with {process.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        return 130
    finally:
        for _, process, _ in children:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        for _, process, log in children:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            log.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
