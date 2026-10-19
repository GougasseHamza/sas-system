import sys
import tempfile
import unittest
from pathlib import Path

from sas_arena.hooks import HookRunner
from sas_arena.models import Risk, TaskKind, TaskLane
from sas_arena.monitoring import MonitorEngine
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, HookSpec, MonitorSpec, ServiceSpec
from sas_arena.store import ArenaStore


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def test_alert_threshold_enqueues_defense_incident_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "rolled-back"
            service = ServiceSpec(
                id="svc",
                name="service",
                root=root,
                descriptor=root / "service.toml",
                hooks={
                    "health": HookSpec(
                        (sys.executable, "-c", "import sys; sys.exit(7)")
                    ),
                    "rollback": HookSpec(
                        (
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('rolled-back').write_text('ok')",
                        )
                    ),
                },
                monitors={
                    "availability": MonitorSpec(
                        hook="health",
                        failure_threshold=2,
                        task_kind=TaskKind.INCIDENT_RESPONSE,
                        lane=TaskLane.DEFENSE,
                        risk=Risk.CRITICAL,
                        auto_rollback=True,
                    )
                },
            )
            event = EventSpec(
                "event",
                "event",
                root / "event.toml",
                {"svc": service},
            )
            store = ArenaStore(root / "arena.db")
            store.initialize()
            store.record_deployment("svc", "r1", str(root / "candidate"), "active", {})
            engine = MonitorEngine(
                event, store, HookRunner(ScopePolicy((root.resolve(),)))
            )
            first = (await engine.run_once())[0]
            second = (await engine.run_once())[0]
            self.assertIsNone(first.task_id)
            self.assertIsNotNone(second.task_id)
            self.assertTrue(marker.is_file())
            self.assertEqual(store.status_counts().get("queued"), 1)
            incident = store.get_task(second.task_id or "")
            self.assertIsNotNone(incident)
            assert incident is not None
            self.assertEqual(incident.lane, TaskLane.DEFENSE)
            self.assertTrue(incident.context["captain_session"])
            self.assertEqual(incident.context["role"], "service-captain")
            self.assertEqual(incident.preferred_provider, "claude")


if __name__ == "__main__":
    unittest.main()
