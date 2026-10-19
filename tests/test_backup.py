import tarfile
import tempfile
import unittest
from pathlib import Path

from sas_arena.backup import create_backup
from sas_arena.store import ArenaStore


class BackupTests(unittest.TestCase):
    def test_consistent_database_and_artifacts_are_archived(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ArenaStore(root / "arena.db")
            store.initialize()
            store.set_event_state("tick", "7")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "proof.txt").write_text("proof\n")
            output = create_backup(store.path, artifacts, root / "backup.tar.gz")
            with tarfile.open(output, "r:gz") as archive:
                names = set(archive.getnames())
            self.assertIn("arena.db", names)
            self.assertIn("manifest.json", names)
            self.assertIn("artifacts/proof.txt", names)


if __name__ == "__main__":
    unittest.main()
