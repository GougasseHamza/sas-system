import sys
import tempfile
import unittest
from pathlib import Path

from sas_arena.defense import DefenseController
from sas_arena.hooks import HookRunner
from sas_arena.scope import ScopePolicy
from sas_arena.specs import HookSpec, ServiceSpec
from sas_arena.store import ArenaStore


class DefenseTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_post_deploy_gate_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "candidate"
            candidate.mkdir()
            service = ServiceSpec(
                "svc",
                "service",
                root,
                root / "service.toml",
                hooks={
                    "verify_patch": HookSpec(
                        (
                            sys.executable,
                            "-c",
                            "import pathlib,sys; assert pathlib.Path(sys.argv[1]).is_dir()",
                            "{candidate_root}",
                        )
                    ),
                    "deploy": HookSpec(
                        (
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('deployed').write_text('yes')",
                        )
                    ),
                    "health": HookSpec(
                        (sys.executable, "-c", "import sys; sys.exit(1)")
                    ),
                    "checker": HookSpec((sys.executable, "-c", "print('ok')")),
                    "rollback": HookSpec(
                        (
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('rollback').write_text('yes')",
                        )
                    ),
                },
            )
            store = ArenaStore(root / "arena.db")
            store.initialize()
            controller = DefenseController(
                store, HookRunner(ScopePolicy((root.resolve(),)))
            )
            result = await controller.deploy(service, candidate, "r1")
            self.assertEqual(result.status, "rolled_back")
            self.assertTrue(result.rolled_back)
            self.assertTrue((root / "rollback").is_file())
            self.assertIsNone(store.active_deployment("svc"))


if __name__ == "__main__":
    unittest.main()
