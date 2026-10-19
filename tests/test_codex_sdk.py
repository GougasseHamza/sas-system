import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openai_codex import Sandbox, SkillInput, TextInput

from sas_arena.cli import providers_from_config
from sas_arena.config import ArenaConfig
from sas_arena.models import Task, TaskKind
from sas_arena.providers.codex_sdk import CodexSdkProvider
from sas_arena.store import ArenaStore

RESULT = {
    "status": "completed",
    "summary": "bounded task completed",
    "confidence": 0.9,
    "claims": [],
    "artifacts": [],
    "next_actions": [],
}


class FakeTurn:
    def __init__(self, result):
        self.result = result
        self.interrupted = False
        self.steered = []

    async def run(self):
        return self.result

    async def interrupt(self):
        self.interrupted = True

    async def steer(self, message):
        self.steered.append(message)


class FakeThread:
    def __init__(self, thread_id: str):
        self.id = thread_id
        self.turn_calls = []

    async def turn(self, input_value, **options):
        self.turn_calls.append((input_value, options))
        return FakeTurn(
            SimpleNamespace(
                final_response=json.dumps(RESULT),
                usage={"totalTokens": 123},
            )
        )


class FakeCodex:
    def __init__(self, config):
        self.config = config
        self.entered = 0
        self.closed = 0
        self.started = []
        self.resumed = []
        self.threads = []
        self.account_type = "chatgpt"

    async def __aenter__(self):
        self.entered += 1
        return self

    async def close(self):
        self.closed += 1

    async def account(self):
        return SimpleNamespace(
            account=SimpleNamespace(root=SimpleNamespace(type=self.account_type))
        )

    async def thread_start(self, **options):
        self.started.append(options)
        thread = FakeThread("thread-new")
        self.threads.append(thread)
        return thread

    async def thread_resume(self, thread_id, **options):
        self.resumed.append((thread_id, options))
        thread = FakeThread(thread_id)
        self.threads.append(thread)
        return thread


class CodexSdkProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_message_is_delivered_to_live_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ArenaStore(root / "arena.db")
            store.initialize()
            task = Task("svc", TaskKind.DIAGNOSE, "diagnose", str(root))
            store.enqueue(task)
            message_id = store.queue_agent_message(task.id, "codex", "check logs")
            provider = CodexSdkProvider(dispatch_database=store.path)
            turn = FakeTurn(SimpleNamespace())
            progress = []

            async def sink(task_value, family, text):
                progress.append((task_value.id, family, text))

            forwarding = asyncio.create_task(
                provider._forward_operator_messages(turn, task, sink)
            )
            await asyncio.sleep(0.6)
            forwarding.cancel()
            await asyncio.gather(forwarding, return_exceptions=True)

            self.assertEqual(turn.steered, ["check logs"])
            events = store.recent_task_events(task.id)
            self.assertTrue(
                any(
                    item["type"] == "agent.message_delivered"
                    and item["payload"]["message_id"] == message_id
                    for item in events
                )
            )
            self.assertTrue(progress)

    async def test_config_defaults_codex_to_sdk_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            config = ArenaConfig(
                Path(temp),
                {"providers": {"codex": {"enabled": True}}},
            )
            providers = providers_from_config(config)
            self.assertIsInstance(providers["codex"], CodexSdkProvider)
            self.assertEqual(providers["codex"].max_concurrency, 0)
            self.assertIsNone(providers["codex"]._semaphore)

    async def test_starts_sdk_thread_with_context_skill_schema_and_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / "skills/arena-offense/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("# offense\n")
            schema = root / "schema.json"
            schema.write_text(json.dumps({"type": "object"}))
            database = root / "arena.db"
            clients = []

            def factory(config):
                client = FakeCodex(config)
                clients.append(client)
                return client

            provider = CodexSdkProvider(
                model="gpt-test",
                reasoning_effort="high",
                dispatch_database=database,
                skills_root=root / "skills",
                client_factory=factory,
            )
            task = Task(
                "svc",
                TaskKind.EXPLOIT_BUILD,
                "build a local synthetic exploit",
                str(root),
                context={"write_access": True},
            )

            result = await provider.execute(task, root, schema)

            self.assertEqual(result.session_id, "thread-new")
            self.assertEqual(len(clients), 1)
            self.assertNotIn("OPENAI_API_KEY", clients[0].config.env)
            self.assertNotIn("CODEX_API_KEY", clients[0].config.env)
            start = clients[0].started[0]
            self.assertEqual(start["cwd"], str(root))
            self.assertEqual(start["sandbox"], Sandbox.workspace_write)
            self.assertEqual(start["model"], "gpt-test")
            dispatch = start["config"]["mcp_servers"]["sas_dispatch"]
            self.assertTrue(dispatch["required"])
            self.assertIn(task.id, dispatch["args"])
            control = start["config"]["mcp_servers"]["sas_control"]
            self.assertTrue(control["required"])
            self.assertIn(task.id, control["args"])
            input_value, options = clients[0].threads[0].turn_calls[0]
            self.assertIsInstance(input_value[0], SkillInput)
            self.assertEqual(input_value[0].name, "arena-offense")
            self.assertIsInstance(input_value[-1], TextInput)
            self.assertIn(task.goal, input_value[-1].text)
            self.assertEqual(options["output_schema"], {"type": "object"})
            await provider.close()
            self.assertEqual(clients[0].closed, 1)

    async def test_resumes_persisted_thread_and_reuses_app_server_client(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schema = root / "schema.json"
            schema.write_text(json.dumps({"type": "object"}))
            clients = []

            def factory(config):
                client = FakeCodex(config)
                clients.append(client)
                return client

            provider = CodexSdkProvider(client_factory=factory)
            for suffix in ("one", "two"):
                task = Task(
                    "svc",
                    TaskKind.DIAGNOSE,
                    f"diagnose {suffix}",
                    str(root),
                    context={"codex_session_id": "thread-svc-r1"},
                    dedupe_key=f"task-{suffix}",
                )
                result = await provider.execute(task, root, schema)
                self.assertEqual(result.session_id, "thread-svc-r1")

            self.assertEqual(len(clients), 1)
            self.assertEqual(clients[0].entered, 1)
            self.assertEqual(len(clients[0].resumed), 2)
            self.assertFalse(clients[0].started)
            await provider.close()

    async def test_chatgpt_auth_mode_fails_closed_before_starting_a_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schema = root / "schema.json"
            schema.write_text(json.dumps({"type": "object"}))
            clients = []

            def factory(config):
                client = FakeCodex(config)
                client.account_type = "apiKey"
                clients.append(client)
                return client

            provider = CodexSdkProvider(client_factory=factory)
            task = Task("svc", TaskKind.DIAGNOSE, "diagnose", str(root))
            with self.assertRaisesRegex(RuntimeError, "not authenticated with ChatGPT"):
                await provider.execute(task, root, schema)
            self.assertFalse(clients[0].started)
            self.assertEqual(clients[0].closed, 1)


if __name__ == "__main__":
    unittest.main()
