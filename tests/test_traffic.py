import json
import sys
import tempfile
import unittest
from pathlib import Path

from sas_arena.hooks import HookRunner
from sas_arena.models import AgentResult, Claim, TaskKind, TaskLane
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, HookSpec, ServiceSpec, TrafficSpec
from sas_arena.store import ArenaStore
from sas_arena.traffic import TrafficEngine, advance_traffic_pipeline


class TrafficTests(unittest.IsolatedAsyncioTestCase):
    async def test_detection_creates_one_bounded_task_per_tick_and_signature(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = {
                "detections": [
                    {
                        "fingerprint": "same-session-shape",
                        "summary": "unexpected private object read",
                        "score": 0.95,
                        "flow_id": "flow-17",
                        "request": "GET /private/17",
                        "response": "FLAG{synthetic}",
                    },
                    {
                        "fingerprint": "noise",
                        "summary": "ordinary health request",
                        "score": 0.2,
                    },
                ]
            }
            service = ServiceSpec(
                id="svc",
                name="service",
                root=root,
                descriptor=root / "service.toml",
                hooks={
                    "traffic": HookSpec(
                        (sys.executable, "-c", f"print({json.dumps(json.dumps(payload))})")
                    )
                },
                traffic=TrafficSpec(hook="traffic", min_score=0.7),
            )
            event = EventSpec(
                "event", "event", root / "event.toml", {"svc": service}
            )
            store = ArenaStore(root / "arena.db")
            store.initialize()
            store.set_event_state("tick", "9")
            engine = TrafficEngine(
                event, store, HookRunner(ScopePolicy((root.resolve(),)))
            )

            first = (await engine.run_once())[0]
            second = (await engine.run_once())[0]

            self.assertEqual(len(first.detections), 1)
            self.assertEqual(first.new_tasks, 1)
            self.assertEqual(second.new_tasks, 0)
            self.assertEqual(store.status_counts().get("queued"), 1)
            task = store.get_task(first.task_ids[0])
            self.assertIsNotNone(task)
            assert task is not None
            self.assertEqual(task.kind, TaskKind.TRAFFIC_TRIAGE)
            self.assertEqual(task.lane, TaskLane.DEFENSE)
            self.assertEqual(task.preferred_provider, "claude")
            self.assertEqual(task.context["traffic_pipeline_stage"], "triage")
            self.assertFalse(task.context["write_access"])
            self.assertEqual(
                task.context["untrusted_observations"][0]["flow_id"], "flow-17"
            )

    async def test_fixed_claude_claude_codex_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            service = ServiceSpec(
                id="svc",
                name="service",
                root=root,
                descriptor=root / "service.toml",
                hooks={"traffic": HookSpec((sys.executable, "-c", "print('{}')"))},
                traffic=TrafficSpec(hook="traffic"),
            )
            event = EventSpec(
                "event", "event", root / "event.toml", {"svc": service}
            )
            store = ArenaStore(root / "arena.db")
            store.initialize()
            engine = TrafficEngine(
                event, store, HookRunner(ScopePolicy((root.resolve(),)))
            )
            detection = {
                "fingerprint": "captured-chain",
                "summary": "suspicious state transition",
                "score": 0.95,
            }
            from sas_arena.traffic import _normalize

            normalized = _normalize(detection)
            self.assertIsNotNone(normalized)
            assert normalized is not None
            triage = engine._task(service, service.traffic, normalized)
            store.enqueue(triage)
            result = AgentResult(
                provider="claude-a",
                status="candidate",
                summary="session needs faithful replay",
                confidence=0.9,
                claims=[
                    Claim(
                        "traffic differs from checker",
                        0.9,
                        [{"kind": "flow", "location": "17", "detail": "novel"}],
                        "replay locally",
                    )
                ],
            )

            advanced = advance_traffic_pipeline(
                store, triage, result, root, provider_family="claude"
            )
            self.assertIsNotNone(advanced)
            reconstruct = store.get_task(advanced[0])  # type: ignore[index]
            self.assertIsNotNone(reconstruct)
            assert reconstruct is not None
            self.assertEqual(reconstruct.kind, TaskKind.TRAFFIC_RECONSTRUCT)
            self.assertEqual(reconstruct.preferred_provider, "claude")

            replay = root / "replay.py"
            replay.write_text("print('synthetic replay')\n")
            result.artifacts = [str(replay)]
            reversed_stage = advance_traffic_pipeline(
                store, reconstruct, result, root, provider_family="claude"
            )
            self.assertIsNotNone(reversed_stage)
            reverse = store.get_task(reversed_stage[0])  # type: ignore[index]
            self.assertIsNotNone(reverse)
            assert reverse is not None
            self.assertEqual(reverse.kind, TaskKind.REVERSE_EXPLOIT)
            self.assertEqual(reverse.preferred_provider, "codex")
            self.assertTrue(reverse.context["captain_session"])


if __name__ == "__main__":
    unittest.main()
