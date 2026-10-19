#!/usr/bin/env python3
"""Verify, deploy, and roll back an OnlyChess patch on the owned VPS lab."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SSH_RUN_ROOT = PROJECT_ROOT / "tools" / "ssh-run"
REMOTE_HELPER = "/usr/local/sbin/sas-arena-remote"


def run(command: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def ssh_identity() -> tuple[str, str]:
    key = os.environ.get(
        "SAS_ARENA_SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519")
    )
    user = os.environ.get("SAS_ARENA_SSH_USER", "root")
    return key, user


def remote_command(host: str, command: str) -> None:
    key, user = ssh_identity()
    run(
        ["go", "run", ".", key, user, host, command],
        cwd=SSH_RUN_ROOT,
        env={**os.environ, "GONOSUMDB": "*", "GOPROXY": "off"},
    )


def upload(host: str, source: Path, destination: str) -> None:
    key, user = ssh_identity()
    run(
        [
            "go",
            "run",
            ".",
            key,
            user,
            host,
            "--upload",
            str(source),
            destination,
        ],
        cwd=SSH_RUN_ROOT,
        env={**os.environ, "GONOSUMDB": "*", "GOPROXY": "off"},
    )


def candidate_bundle(source: Path, revision: str) -> Path:
    required = ("go.mod", "cmd", "internal", "templates")
    missing = [item for item in required if not (source / item).exists()]
    if missing:
        raise ValueError(f"candidate source is missing: {', '.join(missing)}")
    temporary = Path(tempfile.mkdtemp(prefix="sas-onlychess-patch-"))
    binary = temporary / "main"
    environment = {
        **os.environ,
        "GOOS": "linux",
        "GOARCH": "amd64",
        "CGO_ENABLED": "0",
        "GOPROXY": "off",
    }
    run(
        [
            "go",
            "build",
            "-trimpath",
            "-ldflags=-s -w",
            "-o",
            str(binary),
            "./cmd/main.go",
        ],
        cwd=source,
        env=environment,
    )
    shutil.copytree(source / "templates", temporary / "templates")
    archive = temporary / f"{revision}.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(binary, arcname="main")
        bundle.add(temporary / "templates", arcname="templates")
    return archive


def verify(host: str, source: Path, revision: str) -> None:
    archive = candidate_bundle(source, revision)
    remote_root = f"/opt/sas-ad-lab/candidates/{revision}"
    upload(host, archive, f"{remote_root}/source.tar.gz")
    remote_command(host, privileged_command("verify", revision))


def deploy(host: str, revision: str) -> None:
    remote_command(host, privileged_command("deploy", revision))


def rollback(host: str) -> None:
    remote_command(host, privileged_command("rollback"))


def privileged_command(action: str, revision: str | None = None) -> str:
    _, user = ssh_identity()
    prefix = "" if user == "root" else "sudo -n "
    suffix = "" if revision is None else f" {revision}"
    return f"{prefix}{REMOTE_HELPER} {action}{suffix}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("verify", "deploy", "rollback"))
    parser.add_argument("--host", default="147.182.179.38")
    parser.add_argument("--candidate-root")
    parser.add_argument("--revision", default="candidate")
    args = parser.parse_args()
    revision = re.sub(r"[^a-zA-Z0-9_.-]", "-", args.revision)[:80]
    source = Path(args.candidate_root).resolve() if args.candidate_root else None
    if args.action == "verify":
        if source is None:
            raise ValueError("--candidate-root is required for verify")
        verify(args.host, source, revision)
    elif args.action == "deploy":
        deploy(args.host, revision)
    else:
        rollback(args.host)


if __name__ == "__main__":
    main()
