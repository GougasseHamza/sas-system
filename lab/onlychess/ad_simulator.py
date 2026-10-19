#!/usr/bin/env python3
"""Small single-service A/D tick/checker/flag receiver for the OnlyChess lab."""

from __future__ import annotations

import json
import os
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


CHECKER_ROOT = Path(os.environ.get("ONLYCHESS_CHECKER_ROOT", "/opt/sas-ad-lab/checker"))
sys.path.insert(0, str(CHECKER_ROOT))

from cipher import StreamCipher  # noqa: E402
from cipher.utils import base64_to_bit_array, bits_to_string  # noqa: E402
from client import ChessHubClient  # noqa: E402


GIN_LOG = re.compile(
    r"\[GIN\].*?\|\s*(?P<status>\d+)\s*\|.*?\|\s*"
    r"(?P<source>[^| ]+)\s*\|\s*(?P<method>[A-Z]+)\s+\"(?P<path>[^\"]+)\""
)


def credential(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(7)}"


class CompetitionState:
    def __init__(
        self,
        service_url: str,
        state_file: Path,
        round_seconds: int,
        flag_lifetime_rounds: int,
        admin_token: str,
        team_token: str,
        target_ip: str,
    ) -> None:
        self.service_url = service_url.rstrip("/")
        self.state_file = state_file
        self.round_seconds = round_seconds
        self.flag_lifetime_rounds = flag_lifetime_rounds
        self.admin_token = admin_token
        self.team_token = team_token
        self.target_ip = target_ip
        self.lock = threading.RLock()
        self.round_lock = threading.Lock()
        self.tick = 0
        self.status = "STARTING"
        self.last_error = ""
        self.last_round_at = 0.0
        self.flags: list[dict[str, Any]] = []
        self.submissions: dict[str, float] = {}
        self.submission_owners: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            value = json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.tick = int(value.get("tick", 0))
        self.status = str(value.get("status", "STARTING"))
        self.last_error = str(value.get("last_error", ""))
        self.last_round_at = float(value.get("last_round_at", 0))
        self.flags = list(value.get("flags", []))
        self.submissions = {
            str(key): float(item)
            for key, item in dict(value.get("submissions", {})).items()
        }
        self.submission_owners = {
            str(key): str(item)
            for key, item in dict(value.get("submission_owners", {})).items()
        }

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "tick": self.tick,
                    "status": self.status,
                    "last_error": self.last_error,
                    "last_round_at": self.last_round_at,
                    "flags": self.flags,
                    "submissions": self.submissions,
                    "submission_owners": self.submission_owners,
                },
                sort_keys=True,
            )
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_file)

    def public(self) -> dict[str, Any]:
        with self.lock:
            active = self._active_flags()
            latest = active[-1] if active else None
            return {
                "phase": "live",
                "tick": self.tick,
                "round_seconds": self.round_seconds,
                "service": "onlychess",
                "service_status": self.status,
                "last_error": self.last_error[-500:],
                "last_round_at": self.last_round_at,
                "next_tick_at": self.last_round_at + self.round_seconds,
                "active_flags": len(active),
                "accepted_submissions": len(self.submissions),
                "attacker_submissions": dict(
                    sorted(Counter(self.submission_owners.values()).items())
                ),
                "attack_data": None if latest is None else {"game_id": latest["game"]},
            }

    def _active_flags(self, tick: int | None = None) -> list[dict[str, Any]]:
        current_tick = self.tick if tick is None else tick
        minimum = max(1, current_tick - self.flag_lifetime_rounds + 1)
        return [item for item in self.flags if int(item["tick"]) >= minimum]

    def submit(self, flag: str, attacker: str = "anonymous") -> tuple[str, int]:
        with self.lock:
            if self.status != "UP":
                return "own_service_not_up", 409
            if flag in self.submissions:
                return "duplicate", 409
            if not any(item["flag"] == flag for item in self._active_flags()):
                return "invalid_or_expired", 400
            attacker = re.sub(r"[^a-zA-Z0-9_.-]", "-", attacker.strip())[:32]
            if not attacker:
                attacker = "anonymous"
            self.submissions[flag] = time.time()
            self.submission_owners[flag] = attacker
            self._save()
            return "accepted", 200

    def run_round(self) -> dict[str, Any]:
        if not self.round_lock.acquire(blocking=False):
            return {"started": False, "reason": "round already running"}
        try:
            with self.lock:
                next_tick = self.tick + 1
                previous = list(self._active_flags(next_tick))
            stage = "check"
            self._check_service()
            stage = "put"
            entry = self._put_flag(next_tick)
            with self.lock:
                self.tick = next_tick
                self.flags.append(entry)
                self.flags = self.flags[-20:]
                self.last_round_at = time.time()
                self._save()
            stage = "get"
            if previous:
                self._get_flag(secrets.choice(previous))
            with self.lock:
                self.status = "UP"
                self.last_error = ""
                self.last_round_at = time.time()
                self._save()
            return {"started": True, "tick": next_tick, "game_id": entry["game"]}
        except Exception as error:  # checker failures become service status
            with self.lock:
                self.tick = max(self.tick, next_tick)
                self.status = {
                    "check": "DOWN",
                    "put": "MUMBLE",
                    "get": "CORRUPT",
                }.get(stage, "CHECK_FAILED")
                self.last_error = f"{type(error).__name__}: {error}"
                self.last_round_at = time.time()
                self._save()
            return {"started": True, "error": self.last_error}
        finally:
            self.round_lock.release()

    def _check_service(self) -> None:
        with urllib.request.urlopen(
            f"{self.service_url}/open-invites", timeout=5
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"CHECK returned HTTP {response.status}")

    def _put_flag(self, tick: int) -> dict[str, Any]:
        first = ChessHubClient(self.service_url)
        second = ChessHubClient(self.service_url)
        try:
            first_username = credential("arena_a")
            second_username = credential("arena_b")
            first_password = credential("pw")
            second_password = credential("pw")
            alphabet = string.ascii_uppercase + string.digits
            flag = "".join(secrets.choice(alphabet) for _ in range(31)) + "="
            first.register(first_username, first_password)
            first.login(first_username, first_password)
            second.register(second_username, second_password)
            second.login(second_username, second_password)
            first.challenge_player(second_username)
            game_id = second.accept_challenge(first_username)
            first.connect_to_chat_ws(game_id)
            second.connect_to_chat_ws(game_id)
            time.sleep(0.8)
            key = first.get_secret_chat_key(game_id)
            if key != second.get_secret_chat_key(game_id):
                raise RuntimeError("players received different chat keys")
            first.send_private_chat_message(game_id, flag)
            time.sleep(0.8)
            messages = [
                item
                for item in second.get_chat_messages(game_id)
                if item.get("is_private")
            ]
            if flag not in self._decrypt(messages, key):
                raise RuntimeError("PUT verification did not recover the flag")
            return {
                "tick": tick,
                "flag": flag,
                "game": game_id,
                "players": [
                    [first_username, first_password],
                    [second_username, second_password],
                ],
            }
        finally:
            first.close_all_websockets()
            second.close_all_websockets()

    def _get_flag(self, entry: dict[str, Any]) -> None:
        client = ChessHubClient(self.service_url)
        try:
            username, password = entry["players"][0]
            client.login(username, password)
            client.connect_to_chat_ws(entry["game"])
            time.sleep(0.8)
            key = client.get_secret_chat_key(entry["game"])
            messages = [
                item
                for item in client.get_chat_messages(entry["game"])
                if item.get("is_private")
            ]
            if entry["flag"] not in self._decrypt(messages, key):
                raise RuntimeError(f"GET failed for flag from tick {entry['tick']}")
        finally:
            client.close_all_websockets()

    @staticmethod
    def _decrypt(messages: list[dict[str, Any]], key: list[int]) -> list[str]:
        values = []
        for message in messages:
            cipher = StreamCipher(key)
            values.append(
                bits_to_string(
                    cipher.crypt(
                        base64_to_bit_array(message["Content"]), message["iv"]
                    )[0]
                )
            )
        return values


class Handler(BaseHTTPRequestHandler):
    state: CompetitionState

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/health", "/api/state", "/api/scoreboard"}:
            self._json(self.state.public())
            return
        if self.path == "/api/attack-data":
            public = self.state.public()
            self._json(
                {
                    "tick": public["tick"],
                    "service": "onlychess",
                    "attack_data": public["attack_data"],
                }
            )
            return
        if self.path == "/api/client/attack_data":
            public = self.state.public()
            hints = []
            if public["attack_data"] is not None:
                hints = [public["attack_data"]["game_id"]]
            self._json({"onlychess": {self.state.target_ip: hints}})
            return
        if self.path == "/api/traffic":
            self._json({"detections": recent_traffic_detections()})
            return
        self._json({"error": "not found"}, 404)

    def do_PUT(self) -> None:  # noqa: N802
        if self.path != "/flags":
            self._json({"error": "not found"}, 404)
            return
        if not secrets.compare_digest(
            self.headers.get("X-Team-Token", ""), self.state.team_token
        ):
            self._json({"error": "Invalid team token."}, 401)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            flags = json.loads(self.rfile.read(length) or b"[]")
            if not isinstance(flags, list) or not all(
                isinstance(flag, str) for flag in flags
            ):
                raise ValueError("payload must be a JSON flag array")
        except (ValueError, json.JSONDecodeError) as error:
            self._json({"error": str(error)}, 400)
            return
        results = []
        attacker = self.headers.get("X-Attacker", "anonymous")
        for flag in flags[:100]:
            status, _ = self.state.submit(flag, attacker)
            messages = {
                "accepted": "Flag accepted! Earned rehearsal flag points!",
                "duplicate": "Flag is already submitted.",
                "invalid_or_expired": "Flag is invalid or too old.",
                "own_service_not_up": "Your corresponding service is not UP.",
            }
            results.append({"flag": flag, "msg": messages[status]})
        self._json(results)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid JSON"}, 400)
            return
        if self.path == "/api/submit":
            status, code = self.state.submit(
                str(payload.get("flag", "")),
                self.headers.get("X-Attacker", "anonymous"),
            )
            self._json({"status": status}, code)
            return
        if self.path == "/api/tick":
            if not secrets.compare_digest(
                self.headers.get("X-Arena-Token", ""), self.state.admin_token
            ):
                self._json({"error": "unauthorized"}, 401)
                return
            self._json(self.state.run_round())
            return
        self._json({"error": "not found"}, 404)

    def log_message(self, format_value: str, *args: Any) -> None:
        print(f"api {self.client_address[0]} {format_value % args}", flush=True)

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(value, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def recent_traffic_detections() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["podman", "logs", "--since", "8s", "sas-onlychess"],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    detections = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        match = GIN_LOG.search(line)
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
    return detections[-50:]


def tick_loop(state: CompetitionState) -> None:
    while True:
        state.run_round()
        time.sleep(state.round_seconds)


def main() -> None:
    token = os.environ.get("ARENA_ADMIN_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError("ARENA_ADMIN_TOKEN must contain at least 24 characters")
    state = CompetitionState(
        os.environ.get("ONLYCHESS_URL", "http://127.0.0.1:4000"),
        Path(os.environ.get("ARENA_STATE_FILE", "/opt/sas-ad-lab/data/state.json")),
        int(os.environ.get("ARENA_ROUND_SECONDS", "120")),
        int(os.environ.get("ARENA_FLAG_LIFETIME_ROUNDS", "5")),
        token,
        os.environ.get("ARENA_TEAM_TOKEN", "rehearsal-team-token"),
        os.environ.get("ARENA_TARGET_IP", "147.182.179.38"),
    )
    Handler.state = state
    threading.Thread(target=tick_loop, args=(state,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8088), Handler).serve_forever()


if __name__ == "__main__":
    main()
