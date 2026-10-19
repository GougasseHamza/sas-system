import tempfile
import unittest
from pathlib import Path

from sas_arena.cli import _cidr_intersection, build_scope
from sas_arena.config import ArenaConfig
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, ServiceSpec


class ScopeTests(unittest.TestCase):
    def test_rejects_path_outside_authorized_root(self):
        with tempfile.TemporaryDirectory() as temp:
            allowed = Path(temp).resolve()
            policy = ScopePolicy((allowed,))
            self.assertEqual(policy.require_workdir(allowed), allowed)
            with self.assertRaises(PermissionError):
                policy.require_workdir(allowed.parent)

    def test_network_is_disabled_by_default(self):
        policy = ScopePolicy((Path.cwd(),))
        with self.assertRaises(PermissionError):
            policy.require_target("127.0.0.1")

    def test_cidr_scope_uses_actual_network_intersection(self):
        self.assertEqual(
            _cidr_intersection(("10.0.0.0/8",), ("10.60.0.0/16",)),
            ["10.60.0.0/16"],
        )

    def test_event_descriptor_cannot_expand_filesystem_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            config_dir = project / "config"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "arena.toml"
            config_path.write_text(
                """
[scope]
allowed_roots = ["."]
network_enabled = false
allowed_cidrs = []
""".strip()
            )
            outside = root / "outside"
            outside.mkdir()
            service = ServiceSpec(
                "svc", "service", outside, outside / "service.toml", hooks={}
            )
            event = EventSpec(
                "event", "event", project / "event.toml", {"svc": service}
            )
            with self.assertRaises(PermissionError):
                build_scope(ArenaConfig.load(config_path), event)


if __name__ == "__main__":
    unittest.main()
