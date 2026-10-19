import tempfile
import unittest
from pathlib import Path

from sas_arena.flags import FlagBroker
from sas_arena.hooks import HookRunner
from sas_arena.models import AgentResult, Claim, Task, TaskKind
from sas_arena.offense import OffenseController
from sas_arena.promotion import ArtifactStore, PromotionManager
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, ServiceSpec
from sas_arena.store import ArenaStore


class PromotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_exploit_is_copied_promoted_and_farmed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            exploit = workspace / "exploit.py"
            exploit.write_text("print('FLAG{PROMOTED}')\n")
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task(
                "svc",
                TaskKind.EXPLOIT_BUILD,
                "build",
                str(workspace),
                context={"service_revision": "r1"},
            )
            result = AgentResult(
                "codex",
                "candidate",
                "verified",
                0.9,
                claims=[Claim("works", 0.9, [], "farm")],
                artifacts=[str(exploit)],
            )
            capture = PromotionManager(
                store, ArtifactStore(root / "artifacts")
            ).capture(task, result, workspace)
            self.assertIsNotNone(capture.exploit_id)
            active = store.active_exploit("svc")
            self.assertIsNotNone(active)
            assert active is not None
            self.assertTrue(Path(active["artifact_path"]).is_file())

            service = ServiceSpec(
                "svc",
                "service",
                root,
                root / "service.toml",
                hooks={},
                targets=("127.0.0.1",),
            )
            event = EventSpec(
                "event",
                "event",
                root / "event.toml",
                {"svc": service},
                flag_pattern=r"FLAG\{[^}]+\}",
            )
            runner = HookRunner(
                ScopePolicy((root.resolve(),), True, ("127.0.0.0/8",))
            )
            broker = FlagBroker(event, store, runner)
            attack = await OffenseController(event, store, runner, broker).attack(
                service, "127.0.0.1", submit=False
            )
            self.assertEqual(attack.flags_new, 1)

    async def test_verified_patch_enters_manual_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task("svc", TaskKind.PATCH_BUILD, "patch", str(workspace))
            result = AgentResult(
                "claude",
                "candidate",
                "patch",
                0.9,
                claims=[Claim("blocks exploit", 0.9, [], "deploy")],
            )
            capture = PromotionManager(
                store, ArtifactStore(root / "artifacts")
            ).capture(task, result, workspace)
            self.assertIsNotNone(capture.patch_id)
            patch = store.get_patch(capture.patch_id or "")
            self.assertIsNotNone(patch)
            assert patch is not None
            self.assertEqual(patch["status"], "verified")


if __name__ == "__main__":
    unittest.main()
