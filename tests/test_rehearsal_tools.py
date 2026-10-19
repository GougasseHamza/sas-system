from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_admin():
    path = ROOT / "lab" / "onlychess" / "rehearsal_admin.py"
    spec = importlib.util.spec_from_file_location("rehearsal_admin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RehearsalToolTests(unittest.TestCase):
    def test_validates_public_ip_and_ssh_key(self) -> None:
        admin = load_admin()
        self.assertEqual(admin.validate_source_ip("8.8.8.8"), "8.8.8.8")
        self.assertEqual(
            admin.validate_public_key("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 test"),
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 test",
        )

    def test_rejects_non_public_teammate_ip(self) -> None:
        admin = load_admin()
        for address in ("127.0.0.1", "10.0.0.1", "::1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                admin.validate_source_ip(address)

    def test_rejects_multiline_public_key(self) -> None:
        admin = load_admin()
        with self.assertRaises(ValueError):
            admin.validate_public_key("ssh-ed25519 AAAA\nsecond-line")


if __name__ == "__main__":
    unittest.main()
