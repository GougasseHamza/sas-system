#!/usr/bin/env python3
"""Minimal red-team client for the synthetic OnlyChess A/D scoreboard."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


DEFAULT_BASE = os.environ.get(
    "SAS_ARENA_SCOREBOARD_URL", "http://147.182.179.38:8088"
).rstrip("/")
DEFAULT_TOKEN = os.environ.get("SAS_TEAM_TOKEN", "rehearsal-team-token")
DEFAULT_ATTACKER = os.environ.get("SAS_ATTACKER", "anonymous")


def request_json(
    url: str,
    payload: object | None = None,
    *,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            **({"Content-Type": "application/json"} if data else {}),
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--attacker", default=DEFAULT_ATTACKER)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("attack-data")
    submit = commands.add_parser("submit")
    submit.add_argument("flag")
    watch = commands.add_parser("watch")
    watch.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    base = args.base.rstrip("/")
    if args.command == "status":
        print(json.dumps(request_json(f"{base}/api/state"), indent=2, sort_keys=True))
        return 0
    if args.command == "attack-data":
        print(
            json.dumps(
                request_json(f"{base}/api/client/attack_data"),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "submit":
        print(
            json.dumps(
                request_json(
                    f"{base}/flags",
                    [args.flag],
                    method="PUT",
                    headers={
                        "X-Team-Token": args.token,
                        "X-Attacker": args.attacker,
                    },
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    previous = None
    try:
        while True:
            state = request_json(f"{base}/api/state")
            tick = state.get("tick")
            if tick != previous:
                print(json.dumps(state, sort_keys=True), flush=True)
                previous = tick
            time.sleep(max(0.25, args.interval))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
