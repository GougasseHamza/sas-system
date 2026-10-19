#!/usr/bin/env python3
"""Convert recent remote OnlyChess access logs into bounded Arena detections."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SSH_RUN_ROOT = PROJECT_ROOT / "tools" / "ssh-run"
GIN = re.compile(
    r"\[GIN\].*?\|\s*(?P<status>\d+)\s*\|.*?\|\s*"
    r"(?P<source>[^| ]+)\s*\|\s*(?P<method>[A-Z]+)\s+\"(?P<path>[^\"]+)\""
)


def main() -> int:
    host = "147.182.179.38"
    key = os.environ.get(
        "SAS_ARENA_SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519")
    )
    user = os.environ.get("SAS_ARENA_SSH_USER", "root")
    result = subprocess.run(
        [
            "go",
            "run",
            ".",
            key,
            user,
            host,
            "podman logs --since 8s sas-onlychess 2>&1",
        ],
        cwd=SSH_RUN_ROOT,
        env={**os.environ, "GONOSUMDB": "*", "GOPROXY": "off"},
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        print(json.dumps({"error": result.stderr[-1000:]}))
        return 1
    detections = []
    for line in result.stdout.splitlines():
        match = GIN.search(line)
        if match is None:
            continue
        path = match.group("path").split("?", 1)[0]
        method = match.group("method")
        status = int(match.group("status"))
        score = {
            ("POST", "/private-chat"): 0.94,
            ("GET", "/chat"): 0.86,
            ("GET", "/chat-key"): 0.82,
        }.get((method, path), 0.0)
        if score == 0:
            continue
        detections.append(
            {
                "fingerprint": f"onlychess:{method}:{path}:{status}",
                "summary": f"OnlyChess sensitive flow {method} {path} returned {status}",
                "score": score,
                "source_ip": match.group("source"),
                "method": method,
                "path": path,
                "status": status,
                "raw": line[-1000:],
            }
        )
    print(json.dumps({"detections": detections[-50:]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
