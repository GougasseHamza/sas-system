import tempfile
import unittest
from pathlib import Path

from sas_arena.config import ArenaConfig
from sas_arena.dashboard import dashboard_lines
from sas_arena.models import TaskKind
from sas_arena.phases import CompetitionPhase, PhaseController, PhasePolicy
from sas_arena.specs import EventSpec, ServiceSpec
from sas_arena.store import ArenaStore


class PhaseTests(unittest.TestCase):
    def test_grace_seeds_more_codex_work_and_never_auto_starts_live(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            services = {}
            for service_id in ("one", "two"):
                service_root = root / service_id
                service_root.mkdir()
                services[service_id] = ServiceSpec(
                    service_id,
                    service_id,
                    service_root,
                    service_root / "service.toml",
                    hooks={},
                    context={"revision": "r1"},
                )
            event = EventSpec(
                "event", "event", root / "event.toml", services, tick_seconds=120
            )
            config = ArenaConfig(
                root,
                {
                    "phases": {
                        "grace_duration_seconds": 60,
                        "grace": {
                            "cell_workers": 9,
                            "codex_tasks_per_service": 3,
                            "shadow_children_per_codex": 8,
                        },
                        "live": {
                            "cell_workers": 3,
                            "codex_tasks_per_service": 1,
                            "shadow_children_per_codex": 2,
                        },
                    }
                },
            )
            store = ArenaStore(root / "arena.db")
            store.initialize()
            controller = PhaseController(event, store, PhasePolicy.from_config(config))

            grace = controller.start_grace(now=100)
            self.assertEqual(grace["phase"], CompetitionPhase.GRACE)
            self.assertEqual(grace["profile"]["cell_workers"], 9)
            self.assertFalse(grace["profile"]["offense_enabled"])
            self.assertEqual(store.status_counts().get("queued"), 6)
            tasks = store.task_activity(limit=20)
            for item in tasks:
                task = store.get_task(item["task_id"])
                self.assertIsNotNone(task)
                assert task is not None
                self.assertEqual(task.context["shadow_max_children"], 8)
                if task.kind == TaskKind.DIAGNOSE:
                    self.assertEqual(task.preferred_provider, "claude")
                    self.assertEqual(task.context["role"], "service-captain")
                    self.assertTrue(task.context["operator_mode"])
                else:
                    self.assertEqual(task.preferred_provider, "codex")

            expired = controller.snapshot(now=161)
            self.assertEqual(expired["phase"], CompetitionPhase.GRACE)
            self.assertTrue(expired["grace_expired"])

            live = controller.start_live(now=200)
            self.assertEqual(live["phase"], CompetitionPhase.LIVE)
            self.assertEqual(live["tick"], 1)
            self.assertTrue(live["profile"]["offense_enabled"])
            live_tasks = [
                store.get_task(item["task_id"])
                for item in store.task_activity(limit=20)
            ]
            self.assertTrue(
                any(
                    task is not None
                    and task.context.get("phase") == "live"
                    and task.preferred_provider == "claude"
                    and task.context.get("operator_mode")
                    for task in live_tasks
                )
            )
            later = controller.snapshot(now=441)
            self.assertEqual(later["tick"], 3)

            lines = dashboard_lines(controller, store, now=441)
            self.assertIn("phase=LIVE", lines[1])
            self.assertTrue(any("one" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
