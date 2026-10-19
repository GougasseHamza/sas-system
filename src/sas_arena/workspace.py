from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .models import Task


class WorkspaceManager:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root.resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self._git_lock = asyncio.Lock()

    async def prepare(self, task: Task, provider: str, source: Path) -> Path:
        destination = self.run_root / task.id / provider
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return destination

        git_root = await self._git_root(source)
        if git_root is not None:
            async with self._git_lock:
                process = await asyncio.create_subprocess_exec(
                    "git",
                    "-C",
                    str(git_root),
                    "worktree",
                    "add",
                    "--detach",
                    str(destination),
                    "HEAD",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await process.communicate()
                if process.returncode == 0:
                    relative = source.relative_to(git_root)
                    return destination / relative
                raise RuntimeError(
                    f"git worktree creation failed: {stderr.decode().strip()}"
                )

        await asyncio.to_thread(shutil.copytree, source, destination)
        return destination

    async def _git_root(self, source: Path) -> Path | None:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(source),
            "rev-parse",
            "--show-toplevel",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None
        return Path(stdout.decode().strip()).resolve()
