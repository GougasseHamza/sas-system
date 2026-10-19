#!/usr/bin/env python3
"""Launch an offense-only Arena cell for one named rehearsal attacker."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from sas_arena.config import ArenaConfig
from sas_arena.models import Risk, Task, TaskKind, TaskLane
from sas_arena.specs import EventSpec
from sas_arena.store import ArenaStore


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG = "config/arena.onlychess-attacker.toml"
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
        raise RuntimeError(f"cannot launch attacker in phase {phase!r}")


def seed_offense_tasks(name: str) -> None:
    config = ArenaConfig.load(PROJECT_ROOT / CONFIG)
    state = config.section("state")
    store = ArenaStore(config.project_root / state["database"])
    store.initialize()
    service = EventSpec.load(PROJECT_ROOT / EVENT).service("onlychess")
    tasks = (
        (
            TaskKind.SOURCE_MAP,
            "claude",
            False,
            "Map the service entry points, identities, flag-storage path, and "
            "highest-value attack surfaces. Work only from supplied source.",
        ),
        (
            TaskKind.VULN_SCOUT,
            "codex",
            False,
            "Find a concrete exploitable authorization, state-transition, or "
            "cryptographic flaw and provide exact source evidence.",
        ),
        (
            TaskKind.VULN_SCOUT,
            "deepseek",
            False,
            "Independently find a concrete exploitable flaw and prioritize a "
            "path that can recover current flags with low request volume.",
        ),
        (
            TaskKind.EXPLOIT_BUILD,
            "codex",
            True,
            "Build a reliable exploit artifact for the strongest confirmed "
            "finding. It must consume attack data and print recovered flags.",
        ),
    )
    for index, (kind, provider, write_access, goal) in enumerate(tasks):
        store.enqueue(
            Task(
                service=service.id,
                kind=kind,
                lane=TaskLane.OFFENSE,
                goal=goal,
                workdir=str(service.root),
                risk=Risk.HIGH,
                expected_value=0.95 if provider == "codex" else 0.85,
                deadline_seconds=900,
                preferred_provider=provider,
                context={
                    "role": "service-captain" if index == 0 else "offense-specialist",
                    "captain_session": index == 0,
                    "operator_mode": False,
                    "write_access": write_access,
                    "service_revision": str(
                        service.context.get("revision", "unknown")
                    ),
                    "shadow_max_children": 10,
                    "technical_objective": goal,
                    "verification": {"commands": []},
                },
                dedupe_key=f"rehearsal-attacker:{name}:{service.id}:{kind}:{index}",
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    name = args.name.strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9-]{0,20}", name) is None:
        raise ValueError("--name must contain lowercase letters, digits, or hyphens")
    os.environ["SAS_ATTACKER"] = name
    os.environ.setdefault("SAS_TEAM_TOKEN", "rehearsal-team-token")

    checked("doctor")
    checked("event-validate", EVENT)
    ensure_live_phase()
    seed_offense_tasks(name)
    print(
        f"Red cell {name} is live. Traffic monitoring and defense are disabled."
    )
    return subprocess.run(arena("cells", EVENT), cwd=PROJECT_ROOT).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
