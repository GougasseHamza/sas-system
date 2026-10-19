from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .store import ArenaStore


class ServiceControlTools:
    """Service-scoped view and request surface for a running Codex task.

    Mutating calls only enqueue an operator action. They never execute deployment,
    rollback, monitor, attack, or flag-submission hooks from the model process.
    """

    def __init__(self, store: ArenaStore, task_id: str) -> None:
        self.store = store
        self.task_id = task_id

    def _task(self):
        task = self.store.get_task(self.task_id)
        if task is None:
            raise ValueError("parent task does not exist")
        return task

    def service_state(self) -> str:
        task = self._task()
        state = self.store.service_snapshot(task.service)
        state["phase"] = self.store.get_event_state("competition_phase")
        state["pending_operator_actions"] = self.store.list_operator_actions(
            task.service, status="requested", limit=20
        )
        return json.dumps(state, sort_keys=True)

    def recent_evidence(self, limit: int = 20) -> str:
        task = self._task()
        bounded = max(1, min(int(limit), 100))
        return json.dumps(
            {
                "service": task.service,
                "accepted_claims": self.store.latest_claims(task.service, bounded),
                "events": self.store.recent_service_events(task.service, bounded),
            },
            sort_keys=True,
        )

    def traffic_evidence(self, limit: int = 30) -> str:
        task = self._task()
        bounded = max(1, min(int(limit), 100))
        events = self.store.recent_service_events(task.service, 200)
        selected = [
            item
            for item in events
            if any(
                token in str(item["type"]).lower()
                for token in ("traffic", "suricata", "detection", "reconstruct")
            )
        ][:bounded]
        return json.dumps({"service": task.service, "events": selected}, sort_keys=True)

    def verified_patches(self) -> str:
        task = self._task()
        patches = [
            item
            for item in self.store.list_patches(task.service)
            if item["status"] in {"verified", "requested", "deploying"}
        ]
        return json.dumps(patches, sort_keys=True)

    def exploit_registry(self) -> str:
        task = self._task()
        return json.dumps(self.store.list_exploits(task.service), sort_keys=True)

    def request_patch_deploy(self, patch_id: str, reason: str) -> str:
        task = self._task()
        patch = self.store.get_patch(patch_id)
        if patch is None:
            raise ValueError("unknown patch")
        if patch["service"] != task.service:
            raise ValueError("patch belongs to another service")
        if patch["status"] != "verified":
            raise ValueError("only a verified patch can be requested for deployment")
        action_id = self.store.request_operator_action(
            task.service,
            "patch_deploy",
            task.id,
            {"patch_id": patch_id, "reason": _bounded_text(reason)},
        )
        return json.dumps(
            {
                "action_id": action_id,
                "status": "requested",
                "executed": False,
                "next": "deterministic controller/operator approval",
            },
            sort_keys=True,
        )

    def request_rollback(self, reason: str) -> str:
        task = self._task()
        deployment = self.store.active_deployment(task.service)
        if deployment is None:
            raise ValueError("service has no active arena deployment to roll back")
        action_id = self.store.request_operator_action(
            task.service,
            "rollback",
            task.id,
            {
                "deployment_id": deployment["id"],
                "revision": deployment["revision"],
                "reason": _bounded_text(reason),
            },
        )
        return json.dumps(
            {"action_id": action_id, "status": "requested", "executed": False},
            sort_keys=True,
        )

    def request_monitor_check(self, monitor: str, reason: str = "") -> str:
        task = self._task()
        action_id = self.store.request_operator_action(
            task.service,
            "monitor_check",
            task.id,
            {"monitor": _bounded_text(monitor, 128), "reason": _bounded_text(reason)},
        )
        return json.dumps(
            {"action_id": action_id, "status": "requested", "executed": False},
            sort_keys=True,
        )


def build_server(database: Path, parent_task_id: str) -> FastMCP:
    store = ArenaStore(database)
    store.initialize()
    tools = ServiceControlTools(store, parent_task_id)
    server = FastMCP("sas-arena-control")

    server.tool()(tools.service_state)
    server.tool()(tools.recent_evidence)
    server.tool()(tools.traffic_evidence)
    server.tool()(tools.verified_patches)
    server.tool()(tools.exploit_registry)
    server.tool()(tools.request_patch_deploy)
    server.tool()(tools.request_rollback)
    server.tool()(tools.request_monitor_check)
    return server


def _bounded_text(value: Any, limit: int = 1000) -> str:
    return str(value).strip()[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--parent-task", required=True)
    args = parser.parse_args()
    build_server(args.database.resolve(), args.parent_task).run(transport="stdio")


if __name__ == "__main__":
    main()
