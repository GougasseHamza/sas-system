import tempfile
import tomllib
import unittest
from pathlib import Path

from sas_arena.bootstrap import bootstrap_service
from sas_arena.specs import EventSpec


class BootstrapTests(unittest.TestCase):
    def test_unknown_service_generates_descriptor_and_updates_event(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event_dir = root / "event"
            source = root / "released-service"
            event_dir.mkdir()
            source.mkdir()
            event = event_dir / "event.toml"
            event.write_text(
                """
schema_version = 1
id = "rehearsal"
name = "rehearsal"
services = []
[scope]
network_enabled = false
allowed_cidrs = []
""".strip()
                + "\n"
            )
            (source / "compose.yml").write_text("services: {}\n")
            (source / "checker.py").write_text("print('checker')\n")
            (source / "main.go").write_text("package main\n")

            report = bootstrap_service(event, source, "new-service")

            self.assertTrue(report.descriptor.is_file())
            self.assertEqual(report.compose_file, "compose.yml")
            self.assertIn("checker.py", report.checker_candidates)
            self.assertIn("go", report.languages)
            loaded = EventSpec.load(event)
            self.assertEqual(set(loaded.services), {"new-service"})
            self.assertEqual(loaded.validate()[0].split(":", 1)[0], "service new-service")
            with report.descriptor.open("rb") as handle:
                descriptor = tomllib.load(handle)
            self.assertIn("verify_exploit", descriptor["hooks"])
            self.assertFalse(descriptor["cell"]["offense_enabled"])


if __name__ == "__main__":
    unittest.main()
