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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG = "config/arena.onlychess-remote.toml"
EVENT = "events/sas2025-onlychess-remote/event.toml"


def arena(*arguments: str) -> list[str]:
    return ["uv", "run", "sas-arena", "--config", CONFIG, *arguments]


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--no-tui", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    os.environ.setdefault("SAS_TEAM_TOKEN", "rehearsal-team-token")

    checked("doctor")
    checked("event-validate", EVENT)
    checked("monitor-once", EVENT)
    checked("traffic-once", EVENT)
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
        print("Blue team is live. Ctrl-C stops all four background processes.")
        if not args.no_tui:
            return subprocess.run(
                arena("tui", EVENT, "--read-only"), cwd=PROJECT_ROOT
            ).returncode
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
