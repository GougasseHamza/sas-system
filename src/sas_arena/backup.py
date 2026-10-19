from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path


def create_backup(
    database: str | Path,
    artifact_root: str | Path,
    output: str | Path,
) -> Path:
    database = Path(database).resolve()
    artifact_root = Path(artifact_root).resolve()
    output = Path(output).resolve()
    if not database.is_file():
        raise FileNotFoundError(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sas-arena-backup-") as temp:
        staging = Path(temp)
        snapshot = staging / "arena.db"
        source = sqlite3.connect(database)
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        manifest = {
            "created_at": time.time(),
            "database_sha256": _digest(snapshot),
            "artifacts_included": artifact_root.is_dir(),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        temporary = output.with_suffix(output.suffix + ".tmp")
        with tarfile.open(temporary, "w:gz") as archive:
            archive.add(snapshot, arcname="arena.db")
            archive.add(staging / "manifest.json", arcname="manifest.json")
            if artifact_root.is_dir():
                archive.add(artifact_root, arcname="artifacts")
        temporary.replace(output)
    return output


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
