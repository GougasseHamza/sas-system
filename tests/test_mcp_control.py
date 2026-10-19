import json
import tempfile
import unittest
from pathlib import Path

from sas_arena.mcp_control import ServiceControlTools, build_server
from sas_arena.models import Task, TaskKind
from sas_arena.store import ArenaStore


class ServiceControlToolsTests(unittest.TestCase):
    def test_reads_are_scoped_and_patch_deploy_is_only_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task("svc-a", TaskKind.DIAGNOSE, "captain", temp)
            store.enqueue(task)
            patch_id = store.queue_patch(
                "svc-a", "r1", temp, task.id, {"verified": True}
            )
            other_patch = store.queue_patch(
                "svc-b", "r2", temp, task.id, {"verified": True}
            )
            tools = ServiceControlTools(store, task.id)

            patches = json.loads(tools.verified_patches())
            self.assertEqual([item["id"] for item in patches], [patch_id])
            response = json.loads(
                tools.request_patch_deploy(patch_id, "checker and replay passed")
            )
            self.assertFalse(response["executed"])
            action = store.get_operator_action(response["action_id"])
            self.assertIsNotNone(action)
            assert action is not None
            self.assertEqual(action["service"], "svc-a")
            self.assertEqual(action["status"], "requested")
            self.assertEqual(store.get_patch(patch_id)["status"], "verified")
            with self.assertRaisesRegex(ValueError, "another service"):
                tools.request_patch_deploy(other_patch, "cross service")

    def test_control_server_builds_for_existing_task(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            task = Task("svc", TaskKind.REVERSE_EXPLOIT, "reverse", temp)
            store.enqueue(task)
            server = build_server(store.path, task.id)
            self.assertIsNotNone(server)

    def test_operator_action_must_match_source_task_service(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            task = Task("svc-a", TaskKind.DIAGNOSE, "captain", temp)
            store.enqueue(task)
            with self.assertRaisesRegex(ValueError, "does not match"):
                store.request_operator_action(
                    "svc-b", "monitor_check", task.id, {"monitor": "health"}
                )


if __name__ == "__main__":
    unittest.main()
