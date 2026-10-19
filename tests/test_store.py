import tempfile
import unittest
from pathlib import Path

from sas_arena.models import Risk, Task, TaskKind, TaskState
from sas_arena.store import ArenaStore


class StoreTests(unittest.TestCase):
    def test_enqueue_deduplicates_and_leases(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            task = Task("service-a", TaskKind.SOURCE_MAP, "map auth", temp)
            first_id, first_created = store.enqueue(task)
            second_id, second_created = store.enqueue(task)

            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first_id, second_id)
            leased = store.lease_next("worker-1")
            self.assertIsNotNone(leased)
            self.assertEqual(leased.attempts, 1)
            self.assertIsNone(store.lease_next("worker-2"))

    def test_service_cell_leases_only_its_own_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            first = Task("service-a", TaskKind.SOURCE_MAP, "map service a", temp)
            second = Task("service-b", TaskKind.SOURCE_MAP, "map service b", temp)
            store.enqueue(first)
            store.enqueue(second)
            leased = store.lease_next("cell-b", service="service-b")
            self.assertIsNotNone(leased)
            assert leased is not None
            self.assertEqual(leased.service, "service-b")
            remaining = store.lease_next("cell-a", service="service-a")
            self.assertIsNotNone(remaining)
            assert remaining is not None
            self.assertEqual(remaining.service, "service-a")

    def test_critical_tasks_preempt_fifo_within_a_cell(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            normal = Task("svc", TaskKind.SOURCE_MAP, "normal map", temp)
            critical = Task(
                "svc",
                TaskKind.INCIDENT_RESPONSE,
                "critical incident",
                temp,
                risk=Risk.CRITICAL,
                expected_value=1.0,
            )
            store.enqueue(normal)
            store.enqueue(critical)
            leased = store.lease_next("cell", service="svc")
            self.assertIsNotNone(leased)
            assert leased is not None
            self.assertEqual(leased.id, critical.id)

    def test_agent_activity_events_and_operator_message_inbox(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            task = Task("svc", TaskKind.DIAGNOSE, "inspect failure", temp)
            store.enqueue(task)
            store.record_event(
                task.id,
                "agent.progress",
                {"provider_family": "codex", "text": "tool shell started"},
            )
            message_id = store.queue_agent_message(task.id, "codex", "check auth first")

            activity = store.task_activity()
            self.assertEqual(activity[0]["task_id"], task.id)
            events = store.recent_task_events(task.id)
            self.assertTrue(any(item["type"] == "agent.progress" for item in events))
            pending = store.pending_agent_messages(task.id, "codex")
            self.assertEqual(
                pending, [{"id": message_id, "message": "check auth first"}]
            )
            store.finish_agent_message(message_id)
            delivered = store.recent_task_events(task.id)
            self.assertTrue(
                any(item["type"] == "agent.message_delivered" for item in delivered)
            )

    def test_retry_terminal_task_preserves_history_and_resets_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            task = Task("svc", TaskKind.PATCH_BUILD, "repair service", temp)
            store.enqueue(task)
            leased = store.lease_next("worker")
            assert leased is not None
            store.set_state(task.id, TaskState.FAILED)

            self.assertTrue(store.retry_terminal_task(task.id))
            retried = store.lease_next("worker-2")
            assert retried is not None
            self.assertEqual(retried.id, task.id)
            self.assertEqual(retried.attempts, 1)
            events = store.recent_task_events(task.id)
            self.assertTrue(any(item["type"] == "task.retried" for item in events))


if __name__ == "__main__":
    unittest.main()
