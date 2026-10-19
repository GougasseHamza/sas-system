from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CommandOutput:
    returncode: int
    stdout: str
    stderr: str


def resolve_executable(command: str, *fallbacks: str) -> str | None:
    """Resolve a harness command, including common per-user install locations."""
    for candidate in (command, *fallbacks):
        expanded = str(Path(candidate).expanduser())
        resolved = shutil.which(expanded)
        if resolved:
            return resolved
        path = Path(expanded)
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path.resolve())
    return None


async def run_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: str,
    timeout_seconds: int,
) -> CommandOutput:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin.encode()), timeout=timeout_seconds
        )
    except asyncio.CancelledError:
        process.terminate()
        await process.wait()
        raise
    except TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        raise TimeoutError(f"command exceeded {timeout_seconds}s: {argv[0]}")
    return CommandOutput(
        returncode=process.returncode or 0,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )


async def run_streaming_command(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: str,
    timeout_seconds: int,
    stdout_line_sink: Callable[[str], Awaitable[None]] | None = None,
) -> CommandOutput:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(stdin.encode())
    await process.stdin.drain()
    process.stdin.close()

    stdout_lines: list[str] = []

    async def consume_stdout() -> None:
        while True:
            raw = await process.stdout.readline()
            if not raw:
                return
            line = raw.decode(errors="replace")
            stdout_lines.append(line)
            if stdout_line_sink is not None:
                await stdout_line_sink(line)

    consumer = asyncio.create_task(consume_stdout())
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        await consumer
        stderr = await process.stderr.read() if process.stderr is not None else b""
    except asyncio.CancelledError:
        consumer.cancel()
        process.terminate()
        await process.wait()
        raise
    except TimeoutError:
        consumer.cancel()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        raise TimeoutError(f"command exceeded {timeout_seconds}s: {argv[0]}")
    return CommandOutput(
        returncode=process.returncode or 0,
        stdout="".join(stdout_lines),
        stderr=stderr.decode(errors="replace"),
    )
