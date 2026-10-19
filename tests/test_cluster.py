import tempfile
import unittest
from pathlib import Path

from sas_arena.cluster import ClusterSpec, round_robin_plan
from sas_arena.specs import EventSpec, ServiceSpec


class ClusterTests(unittest.TestCase):
    def test_five_nodes_receive_exactly_two_services_with_full_coverage(self):
        services = [f"service-{index}" for index in range(5)]
        nodes = [f"laptop-{index}" for index in range(1, 6)]
        plan = round_robin_plan(services, nodes, services_per_node=2)
        self.assertEqual(set(plan), set(nodes))
        self.assertTrue(all(len(items) == 2 for items in plan.values()))
        self.assertEqual({item for items in plan.values() for item in items}, set(services))

    def test_cluster_enforces_exact_assignment_and_known_services(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            services = {}
            for index in range(5):
                service_id = f"service-{index}"
                service_root = root / service_id
                service_root.mkdir()
                services[service_id] = ServiceSpec(
                    service_id,
                    service_id,
                    service_root,
                    service_root / "service.toml",
                    hooks={},
                )
            event = EventSpec("event", "event", root / "event.toml", services)
            cluster_file = root / "cluster.toml"
            cluster_file.write_text(
                """
[cluster]
expected_nodes = 5
services_per_node = 2
[[nodes]]
id = "laptop-1"
services = ["service-0", "service-1"]
[[nodes]]
id = "laptop-2"
services = ["service-2", "service-3"]
[[nodes]]
id = "laptop-3"
services = ["service-4", "service-0"]
[[nodes]]
id = "laptop-4"
services = ["service-1", "service-2"]
[[nodes]]
id = "laptop-5"
services = ["service-3", "service-4"]
""".strip()
            )
            cluster = ClusterSpec.load(cluster_file)
            assignments = cluster.validate(event)
            self.assertEqual(assignments["laptop-1"], ["service-0", "service-1"])


if __name__ == "__main__":
    unittest.main()
