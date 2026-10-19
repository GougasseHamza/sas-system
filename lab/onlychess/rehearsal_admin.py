#!/usr/bin/env python3
"""Administrative controls for the owned two-machine OnlyChess rehearsal."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SSH_RUN_ROOT = PROJECT_ROOT / "tools" / "ssh-run"
DEFAULT_HOST = "147.182.179.38"
HOST_KEY = "SHA256:wDYo0ZCFN99EbSzVrmF0VnbhvH+ExgwGd8BNtt+8zhg"
REMOTE_HELPER = "/usr/local/sbin/sas-arena-remote"


def ssh_key() -> str:
    return os.environ.get(
        "SAS_ARENA_ADMIN_SSH_KEY",
        os.environ.get(
            "SAS_ARENA_SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519")
        ),
    )


def remote(host: str, command: str) -> None:
    environment = {
        **os.environ,
        "GONOSUMDB": "*",
        "GOPROXY": "off",
        "SAS_ARENA_SSH_HOSTKEY": HOST_KEY,
    }
    subprocess.run(
        ["go", "run", ".", ssh_key(), "root", host, command],
        cwd=SSH_RUN_ROOT,
        env=environment,
        check=True,
    )


def upload(host: str, source: Path, destination: str) -> None:
    environment = {
        **os.environ,
        "GONOSUMDB": "*",
        "GOPROXY": "off",
        "SAS_ARENA_SSH_HOSTKEY": HOST_KEY,
    }
    subprocess.run(
        [
            "go",
            "run",
            ".",
            ssh_key(),
            "root",
            host,
            "--upload",
            str(source),
            destination,
        ],
        cwd=SSH_RUN_ROOT,
        env=environment,
        check=True,
    )


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=8) as response:
        return json.load(response)


def public_status(host: str) -> dict:
    state = get_json(f"http://{host}:8088/api/state")
    with urllib.request.urlopen(f"http://{host}:4000/", timeout=8) as response:
        challenge_status = response.status
    return {"challenge_http": challenge_status, "simulator": state}


def validate_source_ip(value: str) -> str:
    address = ipaddress.ip_address(value)
    if not address.is_global:
        raise ValueError("teammate IP must be a public unicast address")
    return str(address)


def validate_public_key(value: str) -> str:
    key = value.strip()
    if len(key) > 16_384 or "\n" in key:
        raise ValueError("SSH public key must be one bounded line")
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise ValueError("unsupported SSH public key format")
    return key


def onboard(host: str, source_ip: str, public_key_file: Path) -> None:
    source_ip = validate_source_ip(source_ip)
    public_key = validate_public_key(public_key_file.read_text())
    temporary_key = PROJECT_ROOT / ".arena" / "teammate-authorized-key.pub"
    temporary_key.parent.mkdir(parents=True, exist_ok=True)
    temporary_key.write_text(public_key + "\n")
    upload(host, temporary_key, "/tmp/arena-defender-authorized-key.pub")
    sudoers = PROJECT_ROOT / ".arena" / "arena-defender.sudoers"
    sudoers.write_text(
        "arena-defender ALL=(root) NOPASSWD: "
        "/usr/local/sbin/sas-arena-remote verify *, "
        "/usr/local/sbin/sas-arena-remote deploy *, "
        "/usr/local/sbin/sas-arena-remote rollback\n"
    )
    upload(host, sudoers, "/tmp/arena-defender.sudoers")
    quoted_ip = shlex.quote(source_ip)
    remote(
        host,
        "set -eu; "
        "id -u arena-defender >/dev/null 2>&1 || "
        "useradd --create-home --shell /bin/sh arena-defender; "
        "install -d -m 0700 -o arena-defender -g arena-defender "
        "/home/arena-defender/.ssh; "
        "install -m 0600 -o arena-defender -g arena-defender "
        "/tmp/arena-defender-authorized-key.pub "
        "/home/arena-defender/.ssh/authorized_keys; "
        "install -m 0440 -o root -g root /tmp/arena-defender.sudoers "
        "/etc/sudoers.d/arena-defender; visudo -cf /etc/sudoers.d/arena-defender; "
        "install -d -m 2770 -o root -g arena-defender "
        "/opt/sas-ad-lab/candidates; "
        f"ufw allow from {quoted_ip} to any port 4000 proto tcp "
        "comment 'SAS AD red-blue rehearsal'; "
        f"ufw allow from {quoted_ip} to any port 8088 proto tcp "
        "comment 'SAS AD red-blue simulator'; "
        f"ufw route allow from {quoted_ip} to 10.89.0.0/24 port 4000 "
        "proto tcp comment 'SAS AD red-blue container'; "
        "rm -f /tmp/arena-defender-authorized-key.pub "
        "/tmp/arena-defender.sudoers",
    )
    print(
        json.dumps(
            {
                "host": host,
                "source_ip": source_ip,
                "ssh_user": "arena-defender",
                "status": "onboarded",
            },
            indent=2,
        )
    )


def reset(host: str) -> None:
    remote(host, f"{REMOTE_HELPER} reset")
    deadline = time.monotonic() + 120
    last_error = ""
    while time.monotonic() < deadline:
        try:
            state = public_status(host)
            if (
                state["challenge_http"] == 200
                and state["simulator"].get("service_status") == "UP"
                and int(state["simulator"].get("tick", 0)) >= 1
            ):
                print(json.dumps(state, indent=2, sort_keys=True))
                return
        except Exception as error:  # service is expected to restart
            last_error = str(error)
        time.sleep(2)
    raise RuntimeError(f"lab did not recover after reset: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    reset_parser = commands.add_parser("reset")
    reset_parser.add_argument("--confirm", required=True)
    onboard_parser = commands.add_parser("onboard")
    onboard_parser.add_argument("--source-ip", required=True)
    onboard_parser.add_argument("--public-key-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(public_status(args.host), indent=2, sort_keys=True))
        return 0
    if args.command == "reset":
        if args.confirm != "RESET-LAB":
            raise ValueError("reset requires --confirm RESET-LAB")
        reset(args.host)
        return 0
    onboard(args.host, args.source_ip, args.public_key_file)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
