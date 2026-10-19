#!/usr/bin/env python3
"""Build and verify an OnlyChess candidate in a fresh disposable stack."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = LAB_ROOT.parents[1]
COMPOSE_FILE = LAB_ROOT / "compose.candidate.yaml"
CHECKER = LAB_ROOT / "checker_roundtrip.py"


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(command: list[str], *, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def require_source(path: Path) -> None:
    required = ["Dockerfile", "go.mod", "migrations", "templates"]
    missing = [name for name in required if not (path / name).exists()]
    if missing:
        raise ValueError(f"candidate source is missing: {', '.join(missing)}")


def substitute(command: list[str], base_url: str, source: Path) -> list[str]:
    return [
        part.replace("{base_url}", base_url).replace("{source}", str(source))
        for part in command
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument(
        "--must-block-command-json",
        help=(
            "JSON command array for a previously proven exploit. The patch gate "
            "passes this check only when the command returns non-zero. Supports "
            "{base_url} and {source} placeholders."
        ),
    )
    parser.add_argument(
        "--must-block-returncode",
        type=int,
        default=None,
        help=(
            "Expected return code for a positively blocked exploit. When omitted, "
            "any non-zero code is treated as blocked."
        ),
    )
    parser.add_argument("--startup-timeout", type=int, default=180)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    source = Path(args.source_dir).resolve()
    require_source(source)
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    project = f"sas_oc_candidate_{secrets.token_hex(5)}"
    environment = dict(os.environ)
    environment["ONLYCHESS_SOURCE"] = str(source)
    environment["ONLYCHESS_PORT"] = str(port)
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(COMPOSE_FILE),
    ]
    report: dict = {
        "ok": False,
        "project": project,
        "base_url": base_url,
        "checks": [],
    }
    started = time.monotonic()
    try:
        launch = run(
            compose
            + ["up", "-d", "--build", "--wait", "--wait-timeout", str(args.startup_timeout)],
            env=environment,
            timeout=args.startup_timeout + 120,
        )
        report["checks"].append(
            {
                "name": "fresh_stack",
                "passed": launch.returncode == 0,
                "stderr": launch.stderr[-4000:],
            }
        )
        if launch.returncode != 0:
            raise RuntimeError("candidate stack failed to become healthy")

        availability = run(
            [sys.executable, str(CHECKER), "--base-url", base_url],
            env=environment,
            timeout=60,
        )
        report["checks"].append(
            {
                "name": "synthetic_flag_roundtrip",
                "passed": availability.returncode == 0,
                "stdout": availability.stdout[-4000:],
                "stderr": availability.stderr[-4000:],
            }
        )
        if availability.returncode != 0:
            raise RuntimeError("synthetic flag round-trip failed")

        if args.must_block_command_json:
            raw_command = json.loads(args.must_block_command_json)
            if not isinstance(raw_command, list) or not all(
                isinstance(part, str) for part in raw_command
            ):
                raise ValueError("must-block command must be a JSON string array")
            exploit = run(
                substitute(raw_command, base_url, source),
                env=environment,
                timeout=120,
            )
            blocked = (
                exploit.returncode == args.must_block_returncode
                if args.must_block_returncode is not None
                else exploit.returncode != 0
            )
            report["checks"].append(
                {
                    "name": "known_exploit_blocked",
                    "passed": blocked,
                    "returncode": exploit.returncode,
                    "stdout": exploit.stdout[-4000:],
                    "stderr": exploit.stderr[-4000:],
                }
            )
            if not blocked:
                raise RuntimeError("known exploit still succeeds")

        report["ok"] = True
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as error:
        report["error"] = str(error)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(report, sort_keys=True))
        return 1
    finally:
        if not args.keep:
            run(
                compose + ["down", "--volumes", "--remove-orphans"],
                env=environment,
                timeout=60,
            )


if __name__ == "__main__":
    raise SystemExit(main())
