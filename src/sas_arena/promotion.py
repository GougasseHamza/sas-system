from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .models import AgentResult, Task, TaskKind
from .store import ArenaStore


@dataclass(frozen=True, slots=True)
class PromotionCapture:
    exploit_id: str | None = None
    patch_id: str | None = None
    artifacts: tuple[str, ...] = ()


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def capture(
        self,
        task: Task,
        result: AgentResult,
        workspace: Path,
    ) -> tuple[str, ...]:
        workspace = workspace.resolve()
        destination = self.root / task.service / task.id / result.provider
        destination.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for item in result.artifacts:
            source = Path(item)
            if not source.is_absolute():
                source = workspace / source
            source = source.resolve()
            if not source.is_relative_to(workspace) or not source.exists():
                continue
            relative = source.relative_to(workspace)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
            copied.append(str(target))
        manifest = destination / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "task_id": task.id,
                    "service": task.service,
                    "provider": result.provider,
                    "summary": result.summary,
                    "artifacts": copied,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return tuple(copied)


class PromotionManager:
    EXPLOIT_KINDS = {
        TaskKind.EXPLOIT_BUILD,
        TaskKind.EXPLOIT_VERIFY,
        TaskKind.REVERSE_EXPLOIT,
    }
    PATCH_KINDS = {
        TaskKind.PATCH_BUILD,
        TaskKind.PATCH_VERIFY,
        TaskKind.INCIDENT_RESPONSE,
    }

    def __init__(
        self,
        store: ArenaStore,
        artifact_store: ArtifactStore,
        *,
        auto_promote_exploits: bool = True,
    ) -> None:
        self.store = store
        self.artifact_store = artifact_store
        self.auto_promote_exploits = auto_promote_exploits

    def capture(
        self,
        task: Task,
        result: AgentResult,
        workspace: Path,
    ) -> PromotionCapture:
        artifacts = self.artifact_store.capture(task, result, workspace)
        revision = str(task.context.get("service_revision", "candidate"))
        metadata = {
            "provider": result.provider,
            "confidence": result.confidence,
            "summary": result.summary,
            "artifacts": list(artifacts),
        }
        if task.kind in self.EXPLOIT_KINDS:
            if not artifacts:
                self.store.record_event(
                    task.id,
                    "promotion.exploit_missing_artifact",
                    {"kind": task.kind.value},
                )
                return PromotionCapture(artifacts=artifacts)
            version = f"{revision}-{task.id[:8]}-{result.provider}"
            exploit_id = self.store.register_exploit(
                task.service,
                version,
                artifacts[0],
                task.id,
                metadata,
            )
            if self.auto_promote_exploits or bool(
                task.context.get("auto_promote_exploit")
            ):
                self.store.promote_exploit(exploit_id)
            self.store.record_event(
                task.id,
                "promotion.exploit_registered",
                {
                    "exploit_id": exploit_id,
                    "version": version,
                    "active": self.store.active_exploit(task.service) is not None,
                },
            )
            return PromotionCapture(exploit_id=exploit_id, artifacts=artifacts)
        if task.kind in self.PATCH_KINDS:
            patch_id = self.store.queue_patch(
                task.service,
                f"{revision}-{task.id[:8]}",
                str(workspace.resolve()),
                task.id,
                metadata,
            )
            self.store.record_event(
                task.id,
                "promotion.patch_queued",
                {"patch_id": patch_id, "revision": revision},
            )
            return PromotionCapture(patch_id=patch_id, artifacts=artifacts)
        return PromotionCapture(artifacts=artifacts)
