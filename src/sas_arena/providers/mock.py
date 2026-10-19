from __future__ import annotations

import asyncio
import time
from pathlib import Path

from ..models import AgentResult, Claim, Task
from .base import ProgressSink, Provider


class MockProvider(Provider):
    def __init__(
        self,
        name: str,
        *,
        delay_seconds: float = 0.01,
        valid: bool = True,
        progress_messages: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        super().__init__(max_concurrency=20, timeout_seconds=30, instance_id=name)
        self.family = name
        self.delay_seconds = delay_seconds
        self.valid = valid
        self.progress_messages = progress_messages

    async def run(
        self,
        task: Task,
        workspace: Path,
        schema_path: Path,
        progress_sink: ProgressSink | None = None,
    ) -> AgentResult:
        started = time.monotonic()
        if progress_sink is not None:
            for message in self.progress_messages:
                await progress_sink(task, self.name, message)
        await asyncio.sleep(self.delay_seconds)
        marker = workspace / "candidate.ok"
        if self.valid:
            marker.write_text(f"verified candidate from {self.name}\n")
        return AgentResult(
            provider=self.name,
            status="candidate",
            summary=f"Mock candidate from {self.name}",
            confidence=0.9 if self.valid else 0.2,
            claims=[
                Claim(
                    statement="The deterministic candidate marker exists"
                    if self.valid
                    else "No usable candidate was produced",
                    confidence=0.9 if self.valid else 0.2,
                    evidence=[
                        {
                            "kind": "artifact",
                            "location": str(marker),
                            "detail": "candidate marker created by mock provider",
                        }
                    ]
                    if self.valid
                    else [],
                    next_test="Run the configured verification command",
                )
            ],
            artifacts=[str(marker)] if self.valid else [],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
