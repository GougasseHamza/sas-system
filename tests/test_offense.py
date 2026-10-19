import sys
import tempfile
import unittest
from pathlib import Path

from sas_arena.hooks import HookRunner
from sas_arena.offense import OffenseController
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, HookSpec, ServiceSpec
from sas_arena.store import ArenaStore


class OffenseTests(unittest.IsolatedAsyncioTestCase):
    async def test_flags_are_validated_deduplicated_and_submitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = ServiceSpec(
                "svc",
                "service",
                root,
                root / "service.toml",
                hooks={
                    "attack": HookSpec(
                        (sys.executable, "-c", "print('FLAG{synthetic-123}')")
                    )
                },
                targets=("127.0.0.1",),
            )
            submit = HookSpec(
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import sys; Path('submitted').write_text(sys.argv[1])",
                    "{flag}",
                )
            )
            event = EventSpec(
                "event",
                "event",
                root / "event.toml",
                {"svc": service},
                flag_pattern=r"FLAG\{[^}]+\}",
                hooks={"submit": submit},
                network_enabled=True,
                allowed_cidrs=("127.0.0.0/8",),
            )
            store = ArenaStore(root / "arena.db")
            store.initialize()
            controller = OffenseController(
                event,
                store,
                HookRunner(ScopePolicy((root.resolve(),), True, ("127.0.0.0/8",))),
            )
            first = await controller.attack(service, "127.0.0.1")
            second = await controller.attack(service, "127.0.0.1")
            self.assertEqual(first.flags_found, 1)
            self.assertEqual(first.flags_new, 1)
            self.assertEqual(len(first.submissions), 1)
            self.assertEqual(second.flags_new, 0)
            self.assertEqual((root / "submitted").read_text(), "FLAG{synthetic-123}")


if __name__ == "__main__":
    unittest.main()
