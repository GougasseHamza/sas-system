from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Iterable

from .hooks import HookOutcome, HookRunner
from .specs import EventSpec, SpecError
from .store import ArenaStore


class FlagBroker:
    """Event-wide flag ingestion, deduplication, fair leasing, and submission."""

    def __init__(
        self,
        event: EventSpec,
        store: ArenaStore,
        runner: HookRunner,
        *,
        worker_id: str = "flag-broker",
    ) -> None:
        self.event = event
        self.store = store
        self.runner = runner
        self.worker_id = worker_id
        self.flag_regex = re.compile(event.flag_pattern)

    def extract(self, output: str) -> list[str]:
        """Accept NDJSON {flags:[...]} records, with regex extraction as fallback."""
        candidates: list[str] = []
        for line in output.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and isinstance(value.get("flags"), list):
                candidates.extend(
                    item for item in value["flags"] if isinstance(item, str)
                )
        candidates.extend(match.group(0) for match in self.flag_regex.finditer(output))
        return list(
            dict.fromkeys(
                candidate
                for candidate in candidates
                if self.flag_regex.fullmatch(candidate) is not None
            )
        )

    def ingest(
        self,
        service: str,
        target: str,
        flags: Iterable[str],
        *,
        exploit_version: str = "",
        tick: str = "",
    ) -> int:
        expires_at = time.time() + self.event.flag_lifetime_seconds
        created = 0
        for flag in dict.fromkeys(flags):
            if self.flag_regex.fullmatch(flag) is None:
                continue
            digest = hashlib.sha256(flag.encode()).hexdigest()
            created += int(
                self.store.record_flag(
                    digest,
                    service,
                    target,
                    flag,
                    expires_at=expires_at,
                    exploit_version=exploit_version,
                    tick=tick,
                )
            )
        return created

    async def submit_once(
        self,
        *,
        service: str | None = None,
        target: str | None = None,
    ) -> tuple[HookOutcome, ...]:
        if "submit" not in self.event.hooks:
            if self.store.pending_flags(service=service, target=target):
                raise SpecError("event has pending flags but no submit hook")
            return ()
        hook = self.event.hooks["submit"]
        lease_seconds = max(
            self.event.submission_lease_seconds,
            hook.timeout_seconds * self.event.submission_batch_size + 5,
        )
        leased = self.store.lease_flags_fair(
            self.worker_id,
            limit=self.event.submission_batch_size,
            lease_seconds=lease_seconds,
            service=service,
            target=target,
        )
        if leased and any("{flags_json}" in item for item in hook.argv):
            return await self._submit_batch(hook, leased)
        outcomes: list[HookOutcome] = []
        for item in leased:
            try:
                outcome = await self.runner.run(
                    "submit",
                    hook,
                    base_dir=self.event.root,
                    variables={
                        "event_root": str(self.event.root),
                        "service_id": str(item["service"]),
                        "target": str(item["target"]),
                        "flag": str(item["flag"]),
                        "tick": str(item["tick"]),
                        "exploit_version": str(item["exploit_version"]),
                    },
                )
            except Exception as error:  # noqa: BLE001 - retain for broker retry
                self.store.mark_flag(str(item["digest"]), "retry", str(error))
                self.store.record_event(
                    None,
                    "flag.submit_error",
                    {
                        "service": item["service"],
                        "target": item["target"],
                        "error": str(error),
                    },
                )
                continue
            outcomes.append(outcome)
            self.store.mark_flag(
                str(item["digest"]),
                "submitted" if outcome.passed else "retry",
                outcome.stdout or outcome.stderr,
            )
        return tuple(outcomes)

    async def _submit_batch(self, hook, leased: list[dict]) -> tuple[HookOutcome, ...]:
        flags = [str(item["flag"]) for item in leased]
        try:
            outcome = await self.runner.run(
                "submit",
                hook,
                base_dir=self.event.root,
                variables={"flags_json": json.dumps(flags, separators=(",", ":"))},
            )
        except Exception as error:  # noqa: BLE001 - retain for broker retry
            for item in leased:
                self.store.mark_flag(str(item["digest"]), "retry", str(error))
            return ()
        if not outcome.passed:
            for item in leased:
                self.store.mark_flag(
                    str(item["digest"]), "retry", outcome.stdout or outcome.stderr
                )
            return (outcome,)
        try:
            payload = json.loads(outcome.stdout.strip().splitlines()[-1])
            verdicts = {
                str(item["flag"]): bool(item["accepted"])
                for item in payload.get("results", [])
                if isinstance(item, dict) and "flag" in item
            }
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            verdicts = {}
        for item in leased:
            flag = str(item["flag"])
            accepted = verdicts.get(flag)
            status = "submitted" if accepted else "rejected" if accepted is False else "retry"
            self.store.mark_flag(
                str(item["digest"]), status, outcome.stdout or outcome.stderr
            )
        return (outcome,)
