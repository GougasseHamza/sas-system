import tempfile
import unittest
from pathlib import Path

from sas_arena.models import Risk, Task, TaskKind
from sas_arena.shadow import ShadowDispatcher, parse_dispatch_hints
from sas_arena.store import ArenaStore


class ShadowDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def test_parses_bounded_checkpoint(self):
        hints = parse_dispatch_hints(
            'progress DISPATCH_HINT: {"kind":"vuln_scout","goal":"Test anonymous migration membership mutation","evidence":[{"path":"migration.go","line":74}],"priority":0.8,"write_access":false} continuing'
        )
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].kind, TaskKind.VULN_SCOUT)
        self.assertEqual(hints[0].priority, 0.8)

    async def test_codex_checkpoint_enqueues_deduplicated_child(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            parent = Task(
                "service-a",
                TaskKind.EXPLOIT_BUILD,
                "main exploit chain",
                temp,
                risk=Risk.HIGH,
            )
            store.enqueue(parent)
            dispatcher = ShadowDispatcher(store, max_children_per_parent=2)
            message = (
                'DISPATCH_HINT: {"kind":"vuln_scout","goal":"Test anonymous migration membership mutation",'
                '"evidence":[{"path":"migration.go","line":74}],"priority":0.8}'
            )
            await dispatcher.observe(parent, "codex", message)
            await dispatcher.observe(parent, "codex", message)
            self.assertEqual(store.count_children(parent.id), 1)

    async def test_pipeline_codex_parent_can_dispatch_deepseek_children(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            parent = Task(
                "service-a",
                TaskKind.REVERSE_EXPLOIT,
                "reverse captured exploit",
                temp,
                risk=Risk.CRITICAL,
                context={"shadow_generation": 0},
                parent_id="claude-reconstruction-task",
            )
            store.enqueue(parent)
            children = ShadowDispatcher(store).seed_reverse_workers(
                parent, "deepseek-a"
            )
            self.assertEqual(len(children), 3)
            tasks = [store.get_task(child_id) for child_id, created in children]
            self.assertTrue(all(created for _, created in children))
            self.assertTrue(all(task is not None for task in tasks))
            self.assertEqual(
                [task.kind for task in tasks if task is not None],
                [
                    TaskKind.VULN_SCOUT,
                    TaskKind.VULN_SCOUT,
                    TaskKind.EXPLOIT_BUILD,
                ],
            )
            self.assertTrue(
                all(
                    task.preferred_provider == "deepseek-a"
                    for task in tasks
                    if task is not None
                )
            )

    async def test_non_codex_progress_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            parent = Task("service-a", TaskKind.SOURCE_MAP, "map source", temp)
            store.enqueue(parent)
            dispatcher = ShadowDispatcher(store)
            await dispatcher.observe(
                parent,
                "claude",
                'DISPATCH_HINT: {"kind":"vuln_scout","goal":"Test a concrete authorization branch","evidence":[]}',
            )
            self.assertEqual(store.count_children(parent.id), 0)

    async def test_seed_fast_probe_targets_fast_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            parent = Task(
                "service-a",
                TaskKind.DIAGNOSE,
                "analyze chat authorization",
                temp,
                risk=Risk.HIGH,
            )
            store.enqueue(parent)
            dispatcher = ShadowDispatcher(store)
            child_id, created = dispatcher.seed_fast_probe(parent, "claude")
            self.assertTrue(created)
            child = store.get_task(child_id)
            self.assertIsNotNone(child)
            assert child is not None
            self.assertEqual(child.parent_id, parent.id)
            self.assertEqual(child.preferred_provider, "claude")
            self.assertIn("analyze chat authorization", child.goal)

    async def test_seed_patch_candidate_inherits_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArenaStore(Path(temp) / "arena.db")
            store.initialize()
            parent = Task(
                "service-a",
                TaskKind.PATCH_BUILD,
                "patch chat authorization",
                temp,
                risk=Risk.HIGH,
                context={
                    "write_access": True,
                    "verification": {"commands": [["go", "test", "./..."]]},
                },
            )
            store.enqueue(parent)
            child_id, _ = ShadowDispatcher(store).seed_fast_probe(parent, "claude")
            child = store.get_task(child_id)
            self.assertIsNotNone(child)
            assert child is not None
            self.assertEqual(child.kind, TaskKind.PATCH_BUILD)
            self.assertTrue(child.context["write_access"])
            self.assertEqual(
                child.context["verification"]["commands"], [["go", "test", "./..."]]
            )


if __name__ == "__main__":
    unittest.main()
