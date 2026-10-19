import tempfile
import threading
import unittest
import asyncio
from pathlib import Path

import requests

from sas_arena.cluster import ClusterSpec
from sas_arena.config import ArenaConfig
from sas_arena.hub import CoordinatorHub, NodeSynchronizer
from sas_arena.phases import PhaseController, PhasePolicy
from sas_arena.specs import EventSpec, ServiceSpec
from sas_arena.store import ArenaStore


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_phase_and_heartbeat_sync_between_laptops(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service_root = root / "service"
            service_root.mkdir()
            service = ServiceSpec(
                "svc", "service", service_root, service_root / "service.toml", hooks={}
            )
            event = EventSpec(
                "event", "event", root / "event.toml", {"svc": service}
            )
            cluster_file = root / "cluster.toml"
            cluster_file.write_text(
                """
[cluster]
expected_nodes = 1
services_per_node = 1
[[nodes]]
id = "laptop-1"
services = ["svc"]
""".strip()
            )
            cluster = ClusterSpec.load(cluster_file)
            config = ArenaConfig(
                root,
                {
                    "phases": {
                        "grace_duration_seconds": 60,
                        "grace": {"codex_tasks_per_service": 2},
                    }
                },
            )
            policy = PhasePolicy.from_config(config)
            central_store = ArenaStore(root / "central.db")
            central_store.initialize()
            central = PhaseController(event, central_store, policy)
            server = CoordinatorHub(central, central_store, cluster, "secret").make_server(
                "127.0.0.1", 0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            headers = {"Authorization": "Bearer secret"}
            try:
                started = requests.post(
                    f"http://127.0.0.1:{port}/v1/phase",
                    headers=headers,
                    json={"action": "grace", "duration_seconds": 60},
                    timeout=3,
                )
                started.raise_for_status()

                node_store = ArenaStore(root / "node.db")
                node_store.initialize()
                local = PhaseController(
                    event, node_store, policy, service_ids={"svc"}
                )
                synchronizer = NodeSynchronizer(
                    f"http://127.0.0.1:{port}",
                    "secret",
                    cluster.node("laptop-1"),
                    local,
                    node_store,
                )
                await synchronizer.poll_once()

                self.assertEqual(local.snapshot()["phase"], "grace")
                self.assertEqual(node_store.status_counts().get("queued"), 2)
                heartbeats = central_store.node_heartbeats()
                self.assertEqual(heartbeats[0]["node"], "laptop-1")
            finally:
                await asyncio.to_thread(server.shutdown)
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
