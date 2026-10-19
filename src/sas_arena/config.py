from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ArenaConfig:
    project_root: Path
    values: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> ArenaConfig:
        config_path = Path(path).resolve()
        with config_path.open("rb") as handle:
            values = tomllib.load(handle)
        return cls(config_path.parent.parent, values)

    def path(self, section: str, key: str) -> Path:
        return (self.project_root / self.values[section][key]).resolve()

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.values.get(name, {}))
