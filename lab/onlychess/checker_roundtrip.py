#!/usr/bin/env python3
"""Local OnlyChess availability and synthetic flag put/get gate.

This intentionally avoids the competition checksystem dependency while reusing
the official checker client and cipher implementation. It is a local replay
gate, not a network scanner.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKER_ROOT = Path(
    os.environ.get(
        "ONLYCHESS_CHECKER_ROOT",
        PROJECT_ROOT / "lab" / "onlychess" / "checker-source",
    )
)
sys.path.insert(0, str(CHECKER_ROOT))

from cipher import StreamCipher  # noqa: E402
from cipher.utils import base64_to_bit_array, bits_to_string  # noqa: E402
from client import ChessHubClient  # noqa: E402


def credential(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(7)}"


def roundtrip(base_url: str, flag: str, wait_seconds: float) -> dict:
    first = ChessHubClient(base_url)
    second = ChessHubClient(base_url)
    try:
        first_username = credential("arena_a")
        second_username = credential("arena_b")
        first_password = credential("pw")
        second_password = credential("pw")

        first.register(first_username, first_password)
        first.login(first_username, first_password)
        second.register(second_username, second_password)
        second.login(second_username, second_password)

        first.challenge_player(second_username)
        game_id = second.accept_challenge(first_username)
        if not game_id:
            raise AssertionError("invite acceptance did not return a game id")

        first.connect_to_chat_ws(game_id)
        second.connect_to_chat_ws(game_id)
        time.sleep(wait_seconds)

        first_key = first.get_secret_chat_key(game_id)
        second_key = second.get_secret_chat_key(game_id)
        if first_key != second_key:
            raise AssertionError("players received different private-chat keys")

        first.send_private_chat_message(game_id, flag)
        time.sleep(wait_seconds)
        messages = [
            message for message in second.get_chat_messages(game_id)
            if message.get("is_private")
        ]
        if not messages:
            raise AssertionError("second player received no private messages")

        cipher = StreamCipher(first_key)
        decoded_messages = [
            bits_to_string(
                cipher.crypt(
                    base64_to_bit_array(message["Content"]), message["iv"]
                )[0]
            )
            for message in messages
        ]
        if flag not in decoded_messages:
            raise AssertionError("synthetic flag was not recovered from private chat")

        return {
            "ok": True,
            "game_id": game_id,
            "private_messages": len(messages),
            "flag_length": len(flag),
        }
    finally:
        first.close_all_websockets()
        second.close_all_websockets()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--flag", default=None)
    parser.add_argument("--wait-seconds", type=float, default=0.8)
    args = parser.parse_args()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    flag = args.flag or "".join(secrets.choice(alphabet) for _ in range(31)) + "="
    try:
        result = roundtrip(args.base_url.rstrip("/"), flag, args.wait_seconds)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
