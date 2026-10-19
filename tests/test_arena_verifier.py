import sys
import tempfile
import unittest
from pathlib import Path

from sas_arena.hooks import HookRunner
from sas_arena.models import AgentResult, Claim, Task, TaskKind, TaskLane
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, HookSpec, ServiceSpec
from sas_arena.verifier import ArenaVerifier


class ArenaVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_patch_requires_declared_service_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = ServiceSpec(
                "svc", "service", root, root / "service.toml", hooks={}
            )
            event = EventSpec("event", "event", root / "event.toml", {"svc": service})
            verifier = ArenaVerifier(event, HookRunner(ScopePolicy((root.resolve(),))))
            task = Task(
                "svc",
                TaskKind.PATCH_BUILD,
                "patch issue",
                str(root),
                lane=TaskLane.DEFENSE,
            )
            result = AgentResult(
                "mock",
                "candidate",
                "candidate patch",
                0.9,
                claims=[Claim("changed code", 0.9, [], "run verifier")],
            )
            verification = await verifier.verify(task, result, root)
            self.assertFalse(verification.passed)
            self.assertIn("missing mandatory verify_patch", verification.summary)

    async def test_exploit_gate_runs_against_candidate_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "candidate"
            candidate.mkdir()
            (candidate / "proof.ok").write_text("yes")
            service = ServiceSpec(
                "svc",
                "service",
                root,
                root / "service.toml",
                hooks={
                    "verify_exploit": HookSpec(
                        (
                            sys.executable,
                            "-c",
                            "from pathlib import Path; import sys; assert (Path(sys.argv[1]) / 'proof.ok').is_file()",
                            "{candidate_root}",
                        )
                    )
                },
            )
            event = EventSpec("event", "event", root / "event.toml", {"svc": service})
            verifier = ArenaVerifier(event, HookRunner(ScopePolicy((root.resolve(),))))
            task = Task("svc", TaskKind.EXPLOIT_BUILD, "build exploit", str(root))
            result = AgentResult(
                "mock",
                "candidate",
                "candidate exploit",
                0.9,
                claims=[Claim("proof exists", 0.9, [], "replay")],
            )
            verification = await verifier.verify(task, result, candidate)
            self.assertTrue(verification.passed)
            self.assertEqual(verification.summary, "verify_exploit passed")


if __name__ == "__main__":
    unittest.main()
