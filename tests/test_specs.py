import unittest
from pathlib import Path

from sas_arena.specs import EventSpec, SpecError


class SpecTests(unittest.TestCase):
    def test_template_event_is_generic_and_valid(self):
        root = Path(__file__).resolve().parents[1]
        event = EventSpec.load(root / "events/template/event.toml")
        self.assertEqual(event.id, "local-template")
        self.assertEqual(set(event.services), {"service-a"})
        self.assertEqual(event.flag_lifetime_seconds, 300)
        self.assertEqual(event.submission_batch_size, 100)
        self.assertEqual(event.validate()[0].split(":", 1)[0], "service service-a")

    def test_remote_onlychess_rehearsal_descriptor_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        vendor_root = (
            root / "vendor/sasctf-finals-2025/onlychess/service"
        )
        if not vendor_root.exists():
            self.skipTest("optional SAS 2025 replay archive is not installed")
        event = EventSpec.load(
            root / "events/sas2025-onlychess-remote/event.toml"
        )
        messages = event.validate()
        self.assertEqual(event.id, "sas2025-onlychess-remote")
        self.assertEqual(set(event.services), {"onlychess"})
        self.assertTrue(event.network_enabled)
        self.assertTrue(any("service onlychess" in item for item in messages))

    def test_monitor_cannot_reference_missing_hook(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = root / "service.toml"
            service.write_text(
                """
schema_version = 1
id = "svc"
root = "."
[monitors.health]
hook = "missing"
""".strip()
            )
            with self.assertRaises(SpecError):
                from sas_arena.specs import ServiceSpec

                ServiceSpec.load(service)

    def test_traffic_cannot_reference_missing_hook(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = root / "service.toml"
            service.write_text(
                """
schema_version = 1
id = "svc"
root = "."
[traffic]
hook = "missing"
""".strip()
            )
            with self.assertRaises(SpecError):
                from sas_arena.specs import ServiceSpec

                ServiceSpec.load(service)


if __name__ == "__main__":
    unittest.main()
