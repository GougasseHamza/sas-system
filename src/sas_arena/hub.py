from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import requests

from .cluster import ClusterSpec, NodeAssignment
from .phases import PhaseController
from .store import ArenaStore


class CoordinatorHub:
    def __init__(
        self,
        controller: PhaseController,
        store: ArenaStore,
        cluster: ClusterSpec,
        token: str,
    ) -> None:
        if not token:
            raise ValueError("hub token cannot be empty")
        cluster.validate(controller.event)
        self.controller = controller
        self.store = store
        self.cluster = cluster
        self.token = token

    def serve(self, bind: str, port: int) -> None:
        server = self.make_server(bind, port)
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()

    def make_server(self, bind: str, port: int) -> ThreadingHTTPServer:
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                if not self._authorized():
                    return
                if self.path == "/v1/state":
                    self._json(
                        HTTPStatus.OK,
                        {
                            "phase": hub.controller.snapshot(),
                            "assignments": hub.cluster.validate(
                                hub.controller.event
                            ),
                            "nodes": hub.store.node_heartbeats(),
                            "server_time": time.time(),
                        },
                    )
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                if not self._authorized():
                    return
                try:
                    payload = self._payload()
                    if self.path == "/v1/heartbeat":
                        node_id = str(payload.get("node", ""))
                        node = hub.cluster.node(node_id)
                        services = payload.get("services", [])
                        if services != list(node.services):
                            raise ValueError("heartbeat service assignment mismatch")
                        snapshot = payload.get("snapshot", {})
                        if not isinstance(snapshot, dict):
                            raise ValueError("heartbeat snapshot must be an object")
                        hub.store.record_node_heartbeat(node_id, services, snapshot)
                        self._json(HTTPStatus.OK, {"accepted": True})
                        return
                    if self.path == "/v1/phase":
                        action = str(payload.get("action", ""))
                        if action == "grace":
                            state = hub.controller.start_grace(
                                duration_seconds=(
                                    int(payload["duration_seconds"])
                                    if payload.get("duration_seconds") is not None
                                    else None
                                ),
                                seed_tasks=False,
                            )
                        elif action == "live":
                            state = hub.controller.start_live(seed_tasks=False)
                        elif action == "freeze":
                            state = hub.controller.freeze()
                        elif action == "end":
                            state = hub.controller.end()
                        else:
                            raise ValueError(f"unknown phase action: {action!r}")
                        self._json(HTTPStatus.OK, {"phase": state})
                        return
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                except (KeyError, TypeError, ValueError) as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})

            def _authorized(self) -> bool:
                expected = f"Bearer {hub.token}"
                actual = self.headers.get("Authorization", "")
                if hmac.compare_digest(actual, expected):
                    return True
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return False

            def _payload(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1_000_000:
                    raise ValueError("invalid request body length")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("request body must be an object")
                return value

            def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
                body = json.dumps(value, sort_keys=True).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return ThreadingHTTPServer((bind, port), Handler)


class NodeSynchronizer:
    def __init__(
        self,
        url: str,
        token: str,
        node: NodeAssignment,
        controller: PhaseController,
        store: ArenaStore,
        *,
        interval_seconds: float = 1.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.node = node
        self.controller = controller
        self.store = store
        self.interval_seconds = interval_seconds

    async def poll_once(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._poll_sync)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as error:  # noqa: BLE001 - keep node work alive
                self.store.record_event(
                    None,
                    "hub.sync_failed",
                    {"node": self.node.id, "error": str(error)[-2000:]},
                )
            await asyncio.sleep(self.interval_seconds)

    def _poll_sync(self) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        response = requests.get(
            f"{self.url}/v1/state", headers=headers, timeout=3
        )
        response.raise_for_status()
        payload = response.json()
        assignments = payload.get("assignments", {})
        if assignments.get(self.node.id) != list(self.node.services):
            raise RuntimeError("hub assignment does not match local node")
        phase = payload.get("phase")
        if not isinstance(phase, dict):
            raise RuntimeError("hub returned invalid phase state")
        self.controller.apply_remote(phase)
        self._ingest_peer_evidence(payload.get("nodes", []))
        heartbeat = {
            "node": self.node.id,
            "services": list(self.node.services),
            "snapshot": {
                "tasks": self.store.status_counts(),
                "services": {
                    service: {
                        **self.store.service_snapshot(service),
                        "claims": self.store.latest_claims(service, limit=20),
                    }
                    for service in self.node.services
                },
                "phase": self.controller.snapshot(),
            },
        }
        accepted = requests.post(
            f"{self.url}/v1/heartbeat",
            headers={**headers, "Content-Type": "application/json"},
            json=heartbeat,
            timeout=3,
        )
        accepted.raise_for_status()
        return payload

    def _ingest_peer_evidence(self, nodes: object) -> None:
        if not isinstance(nodes, list):
            return
        owned = set(self.node.services)
        for peer in nodes:
            if not isinstance(peer, dict) or peer.get("node") == self.node.id:
                continue
            snapshot = peer.get("snapshot", {})
            services = snapshot.get("services", {}) if isinstance(snapshot, dict) else {}
            if not isinstance(services, dict):
                continue
            for service in owned & set(services):
                evidence = services.get(service, {})
                if not isinstance(evidence, dict):
                    continue
                bounded = {
                    "peer": peer.get("node"),
                    "claims": list(evidence.get("claims", []))[:20],
                    "recent_events": list(evidence.get("recent_events", []))[:20],
                    "active_deployment": evidence.get("active_deployment"),
                }
                digest = hashlib.sha256(
                    json.dumps(bounded, sort_keys=True).encode()
                ).hexdigest()
                key = f"peer_evidence:{peer.get('node')}:{service}"
                if self.store.get_event_state(key) == digest:
                    continue
                self.store.set_event_state(key, digest)
                self.store.record_service_event(
                    service,
                    "peer.evidence_sync",
                    bounded,
                    severity="info",
                )
