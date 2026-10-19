from __future__ import annotations

import os
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True, slots=True)
class HttpEventAdapter:
    tick_url: str
    tick_path: str
    scoreboard_url: str = ""
    attack_info_url: str = ""
    submit_url: str = ""
    submit_method: str = "POST"
    submit_field: str = "flag"
    submit_payload: str = "object"
    accepted_contains: str = ""
    token_env: str = ""
    token_header: str = "Authorization"
    token_prefix: str = "Bearer "
    attacker_env: str = ""
    attacker_header: str = "X-Attacker"
    timeout_seconds: float = 3.0

    @classmethod
    def load(cls, path: str | Path) -> HttpEventAdapter:
        with Path(path).resolve().open("rb") as handle:
            value = tomllib.load(handle)
        http = value.get("http", {})
        if not isinstance(http, dict):
            raise ValueError("adapter http section must be a table")
        tick_url = str(http.get("tick_url", "")).strip()
        tick_path = str(http.get("tick_path", "tick")).strip()
        if not tick_url or not tick_path:
            raise ValueError("http.tick_url and tick_path are required")
        method = str(http.get("submit_method", "POST")).upper()
        if method not in {"POST", "PUT"}:
            raise ValueError("submit_method must be POST or PUT")
        timeout = float(http.get("timeout_seconds", 3))
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        submit_payload = str(http.get("submit_payload", "object")).strip()
        if submit_payload not in {"object", "array"}:
            raise ValueError("submit_payload must be object or array")
        return cls(
            tick_url=tick_url,
            tick_path=tick_path,
            scoreboard_url=str(http.get("scoreboard_url", "")).strip(),
            attack_info_url=str(http.get("attack_info_url", "")).strip(),
            submit_url=str(http.get("submit_url", "")).strip(),
            submit_method=method,
            submit_field=str(http.get("submit_field", "flag")),
            submit_payload=submit_payload,
            accepted_contains=str(http.get("accepted_contains", "")),
            token_env=str(http.get("token_env", "")).strip(),
            token_header=str(http.get("token_header", "Authorization")),
            token_prefix=str(http.get("token_prefix", "Bearer ")),
            attacker_env=str(http.get("attacker_env", "")).strip(),
            attacker_header=str(http.get("attacker_header", "X-Attacker")),
            timeout_seconds=timeout,
        )

    def observe(self) -> dict[str, Any]:
        tick_payload = self._get(self.tick_url)
        tick = _path(tick_payload, self.tick_path)
        if not isinstance(tick, (str, int)):
            raise ValueError("configured tick path did not return a string or integer")
        result: dict[str, Any] = {"tick": tick}
        if self.scoreboard_url:
            result["scoreboard"] = self._get(self.scoreboard_url)
        if self.attack_info_url:
            result["attack_info"] = self._get(self.attack_info_url)
        return result

    def submit(self, flag: str) -> dict[str, Any]:
        return self.submit_many([flag])

    def submit_many(self, flags: list[str]) -> dict[str, Any]:
        if not self.submit_url:
            raise ValueError("adapter has no submit_url")
        if not flags:
            return {"accepted": True, "status": 200, "response": [], "results": []}
        if self.submit_payload == "object" and len(flags) != 1:
            raise ValueError("object submission accepts exactly one flag")
        payload: Any = (
            flags
            if self.submit_payload == "array"
            else {self.submit_field: flags[0]}
        )
        response = requests.request(
            self.submit_method,
            self.submit_url,
            headers=self._headers(required=True),
            json=payload,
            timeout=self.timeout_seconds,
        )
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text[-4000:]
        if not response.ok:
            raise RuntimeError(f"flag submission failed ({response.status_code}): {body}")
        response_items = body if isinstance(body, list) else [body]
        results = []
        for flag in flags:
            matching = next(
                (
                    item
                    for item in response_items
                    if isinstance(item, dict) and item.get("flag") == flag
                ),
                body,
            )
            rendered = json.dumps(matching)
            accepted = (
                not self.accepted_contains or self.accepted_contains in rendered
            )
            results.append(
                {"flag": flag, "accepted": accepted, "response": matching}
            )
        return {
            "accepted": all(item["accepted"] for item in results),
            "status": response.status_code,
            "response": body,
            "results": results,
        }

    def _get(self, url: str) -> Any:
        response = requests.get(
            url, headers=self._headers(required=False), timeout=self.timeout_seconds
        )
        response.raise_for_status()
        return response.json()

    def _headers(self, *, required: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.token_env:
            token = os.environ.get(self.token_env, "")
            if not token and required:
                raise RuntimeError(
                    "event adapter token environment variable is empty: "
                    f"{self.token_env}"
                )
            if token:
                headers[self.token_header] = self.token_prefix + token
        if self.attacker_env:
            attacker = os.environ.get(self.attacker_env, "").strip()
            if attacker:
                headers[self.attacker_header] = attacker
        return headers


def _path(value: Any, path: str) -> Any:
    current = value
    for component in path.split("."):
        if isinstance(current, dict):
            current = current[component]
        elif isinstance(current, list):
            current = current[int(component)]
        else:
            raise KeyError(path)
    return current
