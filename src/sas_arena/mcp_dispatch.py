from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .models import TaskKind
from .shadow import DispatchHint, ShadowDispatcher
from .store import ArenaStore


def build_server(
    database: Path,
    parent_task_id: str,
    *,
    max_children: int,
    child_deadline_seconds: int,
) -> FastMCP:
    store = ArenaStore(database)
    store.initialize()
    dispatcher = ShadowDispatcher(
        store,
        max_children_per_parent=max_children,
        default_deadline_seconds=child_deadline_seconds,
    )
    server = FastMCP("sas-arena-dispatch")

    @server.tool()
    def dispatch_task(
        kind: str,
        goal: str,
        evidence_json: str = "[]",
        priority: float = 0.5,
        write_access: bool = False,
    ) -> str:
        """Queue an independent branch and return immediately.

        Use this only for work Claude/DeepSeek can execute independently while
        the parent Codex session continues its main investigation. Evidence must
        be a JSON array containing only concrete facts already observed.
        """
        parent = store.get_task(parent_task_id)
        if parent is None:
            raise ValueError("parent task does not exist")
        evidence: Any = json.loads(evidence_json)
        if not isinstance(evidence, list) or not all(
            isinstance(item, dict) for item in evidence
        ):
            raise ValueError("evidence_json must encode an array of objects")
        hint = DispatchHint(
            kind=TaskKind(kind),
            goal=goal,
            evidence=tuple(evidence),
            priority=priority,
            write_access=write_access,
        )
        child_id, created = dispatcher.dispatch(parent, hint)
        return json.dumps(
            {"queued": created, "task_id": child_id, "continue_main_task": True}
        )

    @server.tool()
    def get_worker_updates() -> str:
        """Return current states and verified result packets from child workers."""
        updates = store.child_updates(parent_task_id)
        store.record_event(
            parent_task_id,
            "shadow.updates_read",
            {
                "children": len(updates),
                "accepted": sum(item["state"] == "accepted" for item in updates),
            },
        )
        return json.dumps(updates, sort_keys=True)

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--parent-task", required=True)
    parser.add_argument("--max-children", type=int, default=6)
    parser.add_argument("--child-deadline-seconds", type=int, default=180)
    args = parser.parse_args()
    build_server(
        args.database.resolve(),
        args.parent_task,
        max_children=args.max_children,
        child_deadline_seconds=args.child_deadline_seconds,
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
