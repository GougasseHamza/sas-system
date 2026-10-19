from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .models import AgentResult, Task, TaskState, VerificationResult


class ArenaStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS results (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    provider TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    verification_json TEXT,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    service TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    next_test TEXT NOT NULL,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    provider TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    delivered_at REAL
                );

                CREATE TABLE IF NOT EXISTS service_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service TEXT NOT NULL,
                    type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS monitor_state (
                    service TEXT NOT NULL,
                    monitor TEXT NOT NULL,
                    status TEXT NOT NULL,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    consecutive_successes INTEGER NOT NULL DEFAULT 0,
                    alert_open INTEGER NOT NULL DEFAULT 0,
                    last_summary TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (service, monitor)
                );

                CREATE TABLE IF NOT EXISTS deployments (
                    id TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    candidate_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_sessions (
                    service TEXT NOT NULL,
                    role TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (service, role, provider)
                );

                CREATE TABLE IF NOT EXISTS flags (
                    digest TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    target TEXT NOT NULL,
                    flag TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response TEXT NOT NULL,
                    first_seen REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    exploit_version TEXT NOT NULL DEFAULT '',
                    tick TEXT NOT NULL DEFAULT '',
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS event_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS exploits (
                    id TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    version TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_task TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(service, version)
                );

                CREATE TABLE IF NOT EXISTS patch_queue (
                    id TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    candidate_root TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_task TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_actions (
                    id TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_task TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_heartbeats (
                    node TEXT PRIMARY KEY,
                    services_json TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_service_events_service_created
                ON service_events(service, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_claims_service_created
                ON claims(service, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_events_task_id
                ON events(task_id, id);

                CREATE INDEX IF NOT EXISTS idx_agent_messages_pending
                ON agent_messages(task_id, provider, status, id);

                CREATE INDEX IF NOT EXISTS idx_exploits_service_status
                ON exploits(service, status, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_patch_queue_service_status
                ON patch_queue(service, status, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_operator_actions_service_status
                ON operator_actions(service, status, updated_at DESC);

                """
            )
            claim_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(claims)").fetchall()
            }
            if "accepted" not in claim_columns:
                db.execute(
                    "ALTER TABLE claims ADD COLUMN accepted INTEGER NOT NULL DEFAULT 0"
                )
            flag_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(flags)").fetchall()
            }
            flag_migrations = {
                "expires_at": "REAL NOT NULL DEFAULT 0",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "exploit_version": "TEXT NOT NULL DEFAULT ''",
                "tick": "TEXT NOT NULL DEFAULT ''",
                "lease_owner": "TEXT NOT NULL DEFAULT ''",
                "lease_until": "REAL NOT NULL DEFAULT 0",
            }
            for column, declaration in flag_migrations.items():
                if column not in flag_columns:
                    db.execute(f"ALTER TABLE flags ADD COLUMN {column} {declaration}")
            db.execute(
                "UPDATE flags SET expires_at = first_seen + 300 WHERE expires_at = 0"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_flags_queue "
                "ON flags(status, expires_at)"
            )

    def register_exploit(
        self,
        service: str,
        version: str,
        artifact_path: str,
        source_task: str,
        metadata: dict,
    ) -> str:
        exploit_id = str(uuid4())
        now = time.time()
        with self.connect() as db:
            existing = db.execute(
                "SELECT id FROM exploits WHERE service = ? AND version = ?",
                (service, version),
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            db.execute(
                """
                INSERT INTO exploits (
                    id, service, version, artifact_path, status, source_task,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'verified', ?, ?, ?, ?)
                """,
                (
                    exploit_id,
                    service,
                    version,
                    artifact_path,
                    source_task,
                    json.dumps(metadata, sort_keys=True),
                    now,
                    now,
                ),
            )
        return exploit_id

    def promote_exploit(self, exploit_id: str) -> None:
        now = time.time()
        with self.connect() as db:
            row = db.execute(
                "SELECT service FROM exploits WHERE id = ?", (exploit_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown exploit: {exploit_id}")
            service = str(row["service"])
            db.execute(
                "UPDATE exploits SET status = 'retired', updated_at = ? "
                "WHERE service = ? AND status = 'active'",
                (now, service),
            )
            db.execute(
                "UPDATE exploits SET status = 'active', updated_at = ? WHERE id = ?",
                (now, exploit_id),
            )

    def active_exploit(self, service: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM exploits WHERE service = ? AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (service,),
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json"))
        return value

    def list_exploits(self, service: str | None = None) -> list[dict]:
        with self.connect() as db:
            if service is None:
                rows = db.execute(
                    "SELECT * FROM exploits ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM exploits WHERE service = ? ORDER BY updated_at DESC",
                    (service,),
                ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["metadata"] = json.loads(value.pop("metadata_json"))
            values.append(value)
        return values

    def queue_patch(
        self,
        service: str,
        revision: str,
        candidate_root: str,
        source_task: str,
        metadata: dict,
    ) -> str:
        patch_id = str(uuid4())
        now = time.time()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO patch_queue (
                    id, service, revision, candidate_root, status, source_task,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'verified', ?, ?, ?, ?)
                """,
                (
                    patch_id,
                    service,
                    revision,
                    candidate_root,
                    source_task,
                    json.dumps(metadata, sort_keys=True),
                    now,
                    now,
                ),
            )
        return patch_id

    def get_patch(self, patch_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM patch_queue WHERE id = ?", (patch_id,)
            ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["metadata"] = json.loads(value.pop("metadata_json"))
        return value

    def mark_patch(self, patch_id: str, status: str, metadata: dict) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT metadata_json FROM patch_queue WHERE id = ?", (patch_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown patch: {patch_id}")
            previous = json.loads(str(row["metadata_json"]))
            db.execute(
                """
                UPDATE patch_queue SET status = ?, metadata_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps({**previous, **metadata}, sort_keys=True),
                    time.time(),
                    patch_id,
                ),
            )

    def list_patches(self, service: str | None = None) -> list[dict]:
        with self.connect() as db:
            if service is None:
                rows = db.execute(
                    "SELECT * FROM patch_queue ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM patch_queue WHERE service = ? ORDER BY updated_at DESC",
                    (service,),
                ).fetchall()
        values = []
        for row in rows:
            value = dict(row)
            value["metadata"] = json.loads(value.pop("metadata_json"))
            values.append(value)
        return values

    def request_operator_action(
        self,
        service: str,
        kind: str,
        source_task: str,
        payload: dict,
    ) -> str:
        """Queue a service-scoped action for deterministic execution/approval."""
        allowed = {"patch_deploy", "rollback", "monitor_check"}
        if kind not in allowed:
            raise ValueError(f"unsupported operator action: {kind}")
        task = self.get_task(source_task)
        if task is None:
            raise KeyError(f"unknown source task: {source_task}")
        if task.service != service:
            raise ValueError("operator action service does not match source task")
        action_id = str(uuid4())
        now = time.time()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO operator_actions (
                    id, service, kind, status, source_task, payload_json,
                    result_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'requested', ?, ?, '{}', ?, ?)
                """,
                (
                    action_id,
                    service,
                    kind,
                    source_task,
                    json.dumps(payload, sort_keys=True),
                    now,
                    now,
                ),
            )
            self._event(
                db,
                source_task,
                "operator.action_requested",
                {"action_id": action_id, "service": service, "kind": kind},
            )
        return action_id

    def get_operator_action(self, action_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM operator_actions WHERE id = ?", (action_id,)
            ).fetchone()
        return None if row is None else self._operator_action_value(row)

    def list_operator_actions(
        self,
        service: str | None = None,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions: list[str] = []
        parameters: list[object] = []
        if service is not None:
            conditions.append("service = ?")
            parameters.append(service)
        if status is not None:
            conditions.append("status = ?")
            parameters.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        parameters.append(max(1, min(limit, 200)))
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM operator_actions"
                + where
                + " ORDER BY updated_at DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [self._operator_action_value(row) for row in rows]

    def finish_operator_action(
        self,
        action_id: str,
        status: str,
        result: dict,
    ) -> None:
        allowed = {"approved", "executing", "completed", "failed", "rejected"}
        if status not in allowed:
            raise ValueError(f"unsupported operator action status: {status}")
        with self.connect() as db:
            row = db.execute(
                "SELECT source_task FROM operator_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown operator action: {action_id}")
            db.execute(
                """
                UPDATE operator_actions SET status = ?, result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, json.dumps(result, sort_keys=True), time.time(), action_id),
            )
            self._event(
                db,
                str(row["source_task"]),
                f"operator.action_{status}",
                {"action_id": action_id, "result": result},
            )

    @staticmethod
    def _operator_action_value(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        value["result"] = json.loads(value.pop("result_json"))
        return value

    def record_node_heartbeat(
        self, node: str, services: list[str], snapshot: dict
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO node_heartbeats (
                    node, services_json, snapshot_json, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(node) DO UPDATE SET
                    services_json = excluded.services_json,
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (
                    node,
                    json.dumps(services, sort_keys=True),
                    json.dumps(snapshot, sort_keys=True),
                    time.time(),
                ),
            )

    def node_heartbeats(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM node_heartbeats ORDER BY node"
            ).fetchall()
        return [
            {
                "node": str(row["node"]),
                "services": json.loads(str(row["services_json"])),
                "snapshot": json.loads(str(row["snapshot_json"])),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def enqueue(self, task: Task) -> tuple[str, bool]:
        now = time.time()
        with self.connect() as db:
            existing = db.execute(
                "SELECT id FROM tasks WHERE dedupe_key = ?", (task.dedupe_key,)
            ).fetchone()
            if existing:
                return str(existing["id"]), False
            db.execute(
                """
                INSERT INTO tasks (
                    id, dedupe_key, payload_json, state, attempts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.dedupe_key,
                    json.dumps(task.to_dict(), sort_keys=True),
                    TaskState.QUEUED.value,
                    task.attempts,
                    now,
                    now,
                ),
            )
            self._event(db, task.id, "task.enqueued", task.to_dict())
        return task.id, True

    def set_event_state(self, key: str, value: object) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO event_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, sort_keys=True), time.time()),
            )

    def get_event_state(self, key: str) -> object | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT value_json FROM event_state WHERE key = ?", (key,)
            ).fetchone()
            return None if row is None else json.loads(str(row["value_json"]))

    def event_state_snapshot(self) -> dict[str, dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT key, value_json, updated_at FROM event_state ORDER BY key"
            ).fetchall()
            return {
                str(row["key"]): {
                    "value": json.loads(str(row["value_json"])),
                    "updated_at": float(row["updated_at"]),
                }
                for row in rows
            }

    def lease_next(
        self,
        worker: str,
        lease_seconds: int = 900,
        *,
        service: str | None = None,
    ) -> Task | None:
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                UPDATE tasks
                SET state = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE state IN (?, ?) AND lease_until IS NOT NULL AND lease_until < ?
                """,
                (
                    TaskState.QUEUED.value,
                    now,
                    TaskState.LEASED.value,
                    TaskState.RUNNING.value,
                    now,
                ),
            )
            if service is None:
                row = db.execute(
                    """
                    SELECT * FROM tasks
                    WHERE state = ?
                    ORDER BY
                        CASE json_extract(payload_json, '$.risk')
                            WHEN 'critical' THEN 0
                            WHEN 'high' THEN 1
                            WHEN 'normal' THEN 2
                            ELSE 3
                        END,
                        CAST(json_extract(payload_json, '$.expected_value') AS REAL) DESC,
                        created_at ASC
                    LIMIT 1
                    """,
                    (TaskState.QUEUED.value,),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    SELECT * FROM tasks
                    WHERE state = ? AND json_extract(payload_json, '$.service') = ?
                    ORDER BY
                        CASE json_extract(payload_json, '$.risk')
                            WHEN 'critical' THEN 0
                            WHEN 'high' THEN 1
                            WHEN 'normal' THEN 2
                            ELSE 3
                        END,
                        CAST(json_extract(payload_json, '$.expected_value') AS REAL) DESC,
                        created_at ASC
                    LIMIT 1
                    """,
                    (TaskState.QUEUED.value, service),
                ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"]) + 1
            db.execute(
                """
                UPDATE tasks
                SET state = ?, attempts = ?, lease_owner = ?, lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    TaskState.LEASED.value,
                    attempts,
                    worker,
                    now + lease_seconds,
                    now,
                    row["id"],
                ),
            )
            data = json.loads(row["payload_json"])
            data["attempts"] = attempts
            self._event(
                db,
                str(row["id"]),
                "task.leased",
                {"worker": worker, "attempt": attempts},
            )
            return Task.from_dict(data)

    def set_state(
        self, task_id: str, state: TaskState, payload: dict | None = None
    ) -> None:
        now = time.time()
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (state.value, now, task_id),
            )
            self._event(db, task_id, f"task.{state.value}", payload or {})

    def record_result(
        self,
        task: Task,
        result: AgentResult,
        verification: VerificationResult,
        *,
        accepted: bool,
    ) -> str:
        result_id = str(uuid4())
        now = time.time()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO results (
                    id, task_id, provider, payload_json, verification_json, accepted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_id,
                    task.id,
                    result.provider,
                    json.dumps(result.to_dict(include_raw=False), sort_keys=True),
                    json.dumps(
                        {
                            "passed": verification.passed,
                            "summary": verification.summary,
                            "checks": verification.checks,
                        },
                        sort_keys=True,
                    ),
                    int(accepted),
                    now,
                ),
            )
            for claim in result.claims:
                db.execute(
                    """
                    INSERT INTO claims (
                        id, task_id, service, provider, statement, confidence,
                        evidence_json, next_test, created_at
                        , accepted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        task.id,
                        task.service,
                        result.provider,
                        claim.statement,
                        claim.confidence,
                        json.dumps(claim.evidence, sort_keys=True),
                        claim.next_test,
                        now,
                        int(accepted),
                    ),
                )
            self._event(
                db,
                task.id,
                "candidate.accepted" if accepted else "candidate.rejected",
                {
                    "result_id": result_id,
                    "provider": result.provider,
                    "verification": verification.summary,
                },
            )
        return result_id

    def requeue_or_fail(self, task: Task, reason: str) -> TaskState:
        state = (
            TaskState.QUEUED if task.attempts < task.max_attempts else TaskState.FAILED
        )
        now = time.time()
        with self.connect() as db:
            db.execute(
                """
                UPDATE tasks
                SET state = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (state.value, now, task.id),
            )
            self._event(db, task.id, f"task.{state.value}", {"reason": reason})
        return state

    def status_counts(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT state, COUNT(*) AS count FROM tasks GROUP BY state"
            ).fetchall()
            return {str(row["state"]): int(row["count"]) for row in rows}

    def has_active_tasks(self) -> bool:
        active = (
            TaskState.QUEUED.value,
            TaskState.LEASED.value,
            TaskState.RUNNING.value,
            TaskState.VERIFYING.value,
        )
        with self.connect() as db:
            row = db.execute(
                "SELECT 1 FROM tasks WHERE state IN (?, ?, ?, ?) LIMIT 1", active
            ).fetchone()
            return row is not None

    def count_children(self, parent_id: str) -> int:
        with self.connect() as db:
            rows = db.execute("SELECT payload_json FROM tasks").fetchall()
            return sum(
                1
                for row in rows
                if json.loads(row["payload_json"]).get("parent_id") == parent_id
            )

    def get_task(self, task_id: str) -> Task | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload_json, attempts FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            data = json.loads(row["payload_json"])
            data["attempts"] = int(row["attempts"])
            return Task.from_dict(data)

    def child_updates(self, parent_id: str) -> list[dict]:
        updates: list[dict] = []
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, state, payload_json, updated_at FROM tasks ORDER BY created_at"
            ).fetchall()
            for row in rows:
                task_payload = json.loads(row["payload_json"])
                if task_payload.get("parent_id") != parent_id:
                    continue
                result = db.execute(
                    """
                    SELECT provider, payload_json, accepted
                    FROM results WHERE task_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (row["id"],),
                ).fetchone()
                item = {
                    "task_id": str(row["id"]),
                    "state": str(row["state"]),
                    "goal": task_payload["goal"],
                    "updated_at": float(row["updated_at"]),
                }
                if result is not None:
                    payload = json.loads(result["payload_json"])
                    item.update(
                        {
                            "provider": str(result["provider"]),
                            "accepted": bool(result["accepted"]),
                            "summary": payload.get("summary", ""),
                            "claims": payload.get("claims", []),
                            "artifacts": payload.get("artifacts", []),
                        }
                    )
                updates.append(item)
        return updates

    def recent_results(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT task_id, provider, accepted, verification_json, created_at
                FROM results ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def latest_claims(self, service: str, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT provider, statement, confidence, evidence_json, next_test,
                       created_at
                FROM claims WHERE service = ? AND accepted = 1
                ORDER BY created_at DESC LIMIT ?
                """,
                (service, limit),
            ).fetchall()
            return [
                {
                    "provider": str(row["provider"]),
                    "statement": str(row["statement"]),
                    "confidence": float(row["confidence"]),
                    "evidence": json.loads(row["evidence_json"]),
                    "next_test": str(row["next_test"]),
                    "created_at": float(row["created_at"]),
                }
                for row in rows
            ]

    def record_service_event(
        self,
        service: str,
        event_type: str,
        payload: dict,
        *,
        severity: str = "info",
    ) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO service_events (
                    service, type, severity, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    service,
                    event_type,
                    severity,
                    json.dumps(payload, sort_keys=True),
                    time.time(),
                ),
            )
            return int(cursor.lastrowid)

    def recent_service_events(self, service: str, limit: int = 30) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, type, severity, payload_json, created_at
                FROM service_events WHERE service = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (service, limit),
            ).fetchall()
            return [
                {
                    "id": int(row["id"]),
                    "type": str(row["type"]),
                    "severity": str(row["severity"]),
                    "payload": json.loads(row["payload_json"]),
                    "created_at": float(row["created_at"]),
                }
                for row in rows
            ]

    def update_monitor_state(
        self,
        service: str,
        monitor: str,
        *,
        passed: bool,
        summary: str,
        failure_threshold: int,
        recovery_threshold: int,
    ) -> dict:
        now = time.time()
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM monitor_state WHERE service = ? AND monitor = ?",
                (service, monitor),
            ).fetchone()
            failures = 0 if row is None else int(row["consecutive_failures"])
            successes = 0 if row is None else int(row["consecutive_successes"])
            alert_open = False if row is None else bool(row["alert_open"])
            opened = False
            recovered = False
            if passed:
                failures = 0
                successes += 1
                if alert_open and successes >= recovery_threshold:
                    alert_open = False
                    recovered = True
            else:
                successes = 0
                failures += 1
                if not alert_open and failures >= failure_threshold:
                    alert_open = True
                    opened = True
            status = "ok" if passed else "failed"
            db.execute(
                """
                INSERT INTO monitor_state (
                    service, monitor, status, consecutive_failures,
                    consecutive_successes, alert_open, last_summary, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service, monitor) DO UPDATE SET
                    status = excluded.status,
                    consecutive_failures = excluded.consecutive_failures,
                    consecutive_successes = excluded.consecutive_successes,
                    alert_open = excluded.alert_open,
                    last_summary = excluded.last_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    service,
                    monitor,
                    status,
                    failures,
                    successes,
                    int(alert_open),
                    summary,
                    now,
                ),
            )
        return {
            "status": status,
            "consecutive_failures": failures,
            "consecutive_successes": successes,
            "alert_open": alert_open,
            "opened": opened,
            "recovered": recovered,
        }

    def record_deployment(
        self,
        service: str,
        revision: str,
        candidate_root: str,
        status: str,
        metadata: dict,
        *,
        deployment_id: str | None = None,
    ) -> str:
        deployment_id = deployment_id or str(uuid4())
        now = time.time()
        with self.connect() as db:
            if status == "active":
                db.execute(
                    """
                    UPDATE deployments
                    SET status = 'superseded', updated_at = ?
                    WHERE service = ? AND status = 'active' AND id != ?
                    """,
                    (now, service, deployment_id),
                )
            db.execute(
                """
                INSERT INTO deployments (
                    id, service, revision, candidate_root, status, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    deployment_id,
                    service,
                    revision,
                    candidate_root,
                    status,
                    json.dumps(metadata, sort_keys=True),
                    now,
                    now,
                ),
            )
        return deployment_id

    def active_deployment(self, service: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT * FROM deployments
                WHERE service = ? AND status = 'active'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (service,),
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["metadata"] = json.loads(value.pop("metadata_json"))
            return value

    def save_agent_session(
        self,
        service: str,
        role: str,
        provider: str,
        revision: str,
        session_id: str,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO agent_sessions (
                    service, role, provider, revision, session_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(service, role, provider) DO UPDATE SET
                    revision = excluded.revision,
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (service, role, provider, revision, session_id, time.time()),
            )

    def get_agent_session(
        self, service: str, role: str, provider: str, revision: str
    ) -> str | None:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT session_id FROM agent_sessions
                WHERE service = ? AND role = ? AND provider = ? AND revision = ?
                """,
                (service, role, provider, revision),
            ).fetchone()
            return None if row is None else str(row["session_id"])

    def record_flag(
        self,
        digest: str,
        service: str,
        target: str,
        flag: str,
        *,
        expires_at: float | None = None,
        exploit_version: str = "",
        tick: str = "",
    ) -> bool:
        now = time.time()
        expires_at = expires_at if expires_at is not None else now + 300
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO flags (
                    digest, service, target, flag, status, response,
                    first_seen, updated_at, expires_at, exploit_version, tick
                ) VALUES (?, ?, ?, ?, 'pending', '', ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    service,
                    target,
                    flag,
                    now,
                    now,
                    expires_at,
                    exploit_version,
                    tick,
                ),
            )
            return cursor.rowcount == 1

    def pending_flags(
        self,
        limit: int = 100,
        *,
        service: str | None = None,
        target: str | None = None,
    ) -> list[dict]:
        now = time.time()
        conditions = ["status IN ('pending', 'retry')", "expires_at > ?"]
        parameters: list[object] = [now]
        if service is not None:
            conditions.append("service = ?")
            parameters.append(service)
        if target is not None:
            conditions.append("target = ?")
            parameters.append(target)
        parameters.append(limit)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM flags WHERE "
                + " AND ".join(conditions)
                + " ORDER BY expires_at, first_seen LIMIT ?",
                tuple(parameters),
            ).fetchall()
            return [dict(row) for row in rows]

    def lease_flags_fair(
        self,
        worker: str,
        *,
        limit: int,
        lease_seconds: int,
        service: str | None = None,
        target: str | None = None,
    ) -> list[dict]:
        """Lease an expiry-ordered, round-robin batch across service/target groups."""
        now = time.time()
        conditions = [
            "expires_at > ?",
            (
                "(status IN ('pending', 'retry') OR "
                "(status = 'submitting' AND lease_until <= ?))"
            ),
        ]
        parameters: list[object] = [now, now]
        if service is not None:
            conditions.append("service = ?")
            parameters.append(service)
        if target is not None:
            conditions.append("target = ?")
            parameters.append(target)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                UPDATE flags SET status = 'expired', updated_at = ?,
                    lease_owner = '', lease_until = 0
                WHERE status IN ('pending', 'retry', 'submitting')
                  AND expires_at <= ?
                """,
                (now, now),
            )
            rows = db.execute(
                "SELECT * FROM flags WHERE "
                + " AND ".join(conditions)
                + " ORDER BY expires_at, first_seen",
                tuple(parameters),
            ).fetchall()
            groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for row in rows:
                groups.setdefault((str(row["service"]), str(row["target"])), []).append(
                    row
                )
            selected: list[sqlite3.Row] = []
            keys = sorted(
                groups,
                key=lambda key: (float(groups[key][0]["expires_at"]), key),
            )
            while keys and len(selected) < limit:
                next_keys: list[tuple[str, str]] = []
                for key in keys:
                    selected.append(groups[key].pop(0))
                    if groups[key]:
                        next_keys.append(key)
                    if len(selected) >= limit:
                        break
                keys = next_keys
            lease_until = now + lease_seconds
            for row in selected:
                db.execute(
                    """
                    UPDATE flags SET status = 'submitting', lease_owner = ?,
                        lease_until = ?, updated_at = ? WHERE digest = ?
                    """,
                    (worker, lease_until, now, str(row["digest"])),
                )
            return [dict(row) for row in selected]

    def mark_flag(self, digest: str, status: str, response: str) -> None:
        with self.connect() as db:
            db.execute(
                """
                UPDATE flags SET status = ?, response = ?, updated_at = ?,
                    attempts = attempts + 1, lease_owner = '', lease_until = 0
                WHERE digest = ?
                """,
                (status, response[-4000:], time.time(), digest),
            )

    def service_snapshot(self, service: str) -> dict:
        with self.connect() as db:
            monitor_rows = db.execute(
                """
                SELECT monitor, status, consecutive_failures, consecutive_successes,
                       alert_open, last_summary, updated_at
                FROM monitor_state WHERE service = ? ORDER BY monitor
                """,
                (service,),
            ).fetchall()
            task_rows = db.execute(
                """
                SELECT state, COUNT(*) AS count FROM tasks
                WHERE json_extract(payload_json, '$.service') = ?
                GROUP BY state
                """,
                (service,),
            ).fetchall()
            flag_rows = db.execute(
                """
                SELECT status, COUNT(*) AS count FROM flags
                WHERE service = ? GROUP BY status
                """,
                (service,),
            ).fetchall()
        return {
            "service": service,
            "tasks": {str(row["state"]): int(row["count"]) for row in task_rows},
            "monitors": [dict(row) for row in monitor_rows],
            "active_deployment": self.active_deployment(service),
            "flags": {str(row["status"]): int(row["count"]) for row in flag_rows},
            "recent_events": self.recent_service_events(service, limit=10),
        }

    def record_event(self, task_id: str | None, event_type: str, payload: dict) -> None:
        with self.connect() as db:
            self._event(db, task_id, event_type, payload)

    def recent_task_events(
        self, task_id: str, *, after_id: int = 0, limit: int = 200
    ) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT id, task_id, type, payload_json, created_at
                FROM events
                WHERE task_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (task_id, after_id, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "task_id": str(row["task_id"]),
                "type": str(row["type"]),
                "payload": json.loads(str(row["payload_json"])),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def task_activity(self, *, active_only: bool = True, limit: int = 50) -> list[dict]:
        active = (
            TaskState.QUEUED.value,
            TaskState.LEASED.value,
            TaskState.RUNNING.value,
            TaskState.VERIFYING.value,
        )
        where = "WHERE state IN (?, ?, ?, ?)" if active_only else ""
        params: tuple = active if active_only else ()
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT id, state, attempts, lease_owner, lease_until,
                       payload_json, created_at, updated_at
                FROM tasks {where}
                ORDER BY updated_at DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        activity: list[dict] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            activity.append(
                {
                    "task_id": str(row["id"]),
                    "service": payload.get("service", ""),
                    "kind": payload.get("kind", ""),
                    "goal": payload.get("goal", ""),
                    "state": str(row["state"]),
                    "attempts": int(row["attempts"]),
                    "worker": row["lease_owner"],
                    "lease_until": row["lease_until"],
                    "created_at": float(row["created_at"]),
                    "updated_at": float(row["updated_at"]),
                }
            )
        return activity

    def task_state(self, task_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT state FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return None if row is None else str(row["state"])

    def queue_agent_message(self, task_id: str, provider: str, message: str) -> int:
        if not message.strip():
            raise ValueError("agent message cannot be empty")
        with self.connect() as db:
            exists = db.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"unknown task: {task_id}")
            cursor = db.execute(
                """
                INSERT INTO agent_messages (
                    task_id, provider, message, status, created_at
                ) VALUES (?, ?, ?, 'queued', ?)
                """,
                (task_id, provider, message, time.time()),
            )
            message_id = int(cursor.lastrowid)
            self._event(
                db,
                task_id,
                "agent.message_queued",
                {"message_id": message_id, "provider": provider},
            )
        return message_id

    def pending_agent_messages(
        self, task_id: str, provider: str, *, limit: int = 10
    ) -> list[dict]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT id, message FROM agent_messages
                WHERE task_id = ? AND provider = ? AND status = 'queued'
                ORDER BY id ASC LIMIT ?
                """,
                (task_id, provider, limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute(
                    f"UPDATE agent_messages SET status = 'sending' "
                    f"WHERE id IN ({placeholders})",
                    ids,
                )
        return [{"id": int(row["id"]), "message": str(row["message"])} for row in rows]

    def finish_agent_message(self, message_id: int, *, error: str = "") -> None:
        status = "failed" if error else "delivered"
        with self.connect() as db:
            row = db.execute(
                "SELECT task_id, provider FROM agent_messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return
            db.execute(
                """
                UPDATE agent_messages
                SET status = ?, error = ?, delivered_at = ? WHERE id = ?
                """,
                (status, error[-2000:], time.time(), message_id),
            )
            self._event(
                db,
                str(row["task_id"]),
                f"agent.message_{status}",
                {
                    "message_id": message_id,
                    "provider": str(row["provider"]),
                    "error": error[-500:],
                },
            )

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        task_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        db.execute(
            "INSERT INTO events (task_id, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (task_id, event_type, json.dumps(payload, sort_keys=True), time.time()),
        )
