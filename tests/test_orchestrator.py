import tempfile
import unittest
from pathlib import Path

from sas_arena.models import Risk, Task, TaskKind
from sas_arena.orchestrator import Orchestrator
from sas_arena.providers import MockProvider
from sas_arena.router import LatencyRouter
from sas_arena.scope import ScopePolicy
from sas_arena.shadow import ShadowDispatcher
from sas_arena.store import ArenaStore
from sas_arena.verifier import CommandVerifier
from sas_arena.workspace import WorkspaceManager


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    def test_named_provider_resumes_family_session_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ArenaStore(root / "arena.db")
            store.initialize()
            provider = MockProvider("deepseek-a")
            provider.family = "deepseek"
            orchestrator = Orchestrator(
                store=store,
                providers={"deepseek-a": provider},
                router=LatencyRouter(0),
                verifier=CommandVerifier(),
                workspace_manager=WorkspaceManager(root / "worktrees"),
                scope=ScopePolicy((root.resolve(),)),
                schema_path=Path(__file__).resolve().parents[1]
                / "schemas/agent_result.schema.json",
            )
            task = Task(
                "svc",
                TaskKind.DIAGNOSE,
                "resume captain",
                str(root),
                context={
                    "captain_session": True,
                    "role": "captain",
                    "service_revision": "r1",
                },
            )
            store.save_agent_session("svc", "captain", "deepseek-a", "r1", "session-1")
            resumed = orchestrator._with_persistent_session(task, "deepseek-a")
            self.assertEqual(resumed.context["deepseek_session_id"], "session-1")

    async def test_first_verified_candidate_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "input.txt").write_text("test\n")
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task(
                "service-a",
                TaskKind.EXPLOIT_BUILD,
                "produce marker",
                str(source),
                risk=Risk.HIGH,
                expected_value=0.9,
                context={
                    "write_access": True,
                    "verification": {
                        "commands": [
                            [
                                "python3",
                                "-c",
                                "from pathlib import Path; assert Path('candidate.ok').is_file()",
                            ]
                        ]
                    },
                },
            )
            store.enqueue(task)
            project = Path(__file__).resolve().parents[1]
            orchestrator = Orchestrator(
                store=store,
                providers={
                    "deepseek": MockProvider(
                        "deepseek", delay_seconds=0.01, valid=False
                    ),
                    "claude": MockProvider("claude", delay_seconds=0.02, valid=True),
                    "codex": MockProvider("codex", delay_seconds=1.0, valid=True),
                },
                router=LatencyRouter(0),
                verifier=CommandVerifier(),
                workspace_manager=WorkspaceManager(root / "worktrees"),
                scope=ScopePolicy((root.resolve(),)),
                schema_path=project / "schemas/agent_result.schema.json",
                max_parallel_candidates=3,
            )
            await orchestrator.run_once("test")
            self.assertEqual(store.status_counts().get("accepted"), 1)
            results = store.recent_results()
            self.assertTrue(any(row["accepted"] for row in results))
            self.assertFalse(any(row["provider"] == "codex" for row in results))

    async def test_accepted_claude_triage_automatically_starts_reconstruction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task(
                "service-a",
                TaskKind.TRAFFIC_TRIAGE,
                "classify captured session",
                str(source),
                context={"traffic_pipeline_stage": "triage"},
                preferred_provider="claude",
            )
            store.enqueue(task)
            provider = MockProvider("claude")
            provider.family = "claude"
            orchestrator = Orchestrator(
                store=store,
                providers={"claude": provider},
                router=LatencyRouter(0),
                verifier=CommandVerifier(),
                workspace_manager=WorkspaceManager(root / "worktrees"),
                scope=ScopePolicy((root.resolve(),)),
                schema_path=Path(__file__).resolve().parents[1]
                / "schemas/agent_result.schema.json",
            )

            await orchestrator.run_once("traffic-test")

            children = store.child_updates(task.id)
            self.assertEqual(len(children), 1)
            reconstruction = store.get_task(children[0]["task_id"])
            self.assertIsNotNone(reconstruction)
            assert reconstruction is not None
            self.assertEqual(reconstruction.kind, TaskKind.TRAFFIC_RECONSTRUCT)
            self.assertEqual(reconstruction.preferred_provider, "claude")

    async def test_reverse_captain_starts_three_deepseek_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task(
                "service-a",
                TaskKind.REVERSE_EXPLOIT,
                "reverse proven replay",
                str(source),
                risk=Risk.CRITICAL,
                context={"traffic_pipeline_stage": "reverse"},
                preferred_provider="codex",
                parent_id="claude-reconstruction",
            )
            store.enqueue(task)
            codex = MockProvider("codex")
            codex.family = "codex"
            deepseek = MockProvider("deepseek-a")
            deepseek.family = "deepseek"
            orchestrator = Orchestrator(
                store=store,
                providers={"codex": codex, "deepseek-a": deepseek},
                router=LatencyRouter(0),
                verifier=CommandVerifier(),
                workspace_manager=WorkspaceManager(root / "worktrees"),
                scope=ScopePolicy((root.resolve(),)),
                schema_path=Path(__file__).resolve().parents[1]
                / "schemas/agent_result.schema.json",
                shadow_dispatcher=ShadowDispatcher(store),
            )

            await orchestrator.run_once("reverse-test")

            children = store.child_updates(task.id)
            self.assertEqual(len(children), 3)
            self.assertTrue(
                all(
                    store.get_task(child["task_id"]).preferred_provider
                    == "deepseek-a"
                    for child in children
                )
            )


if __name__ == "__main__":
    unittest.main()
