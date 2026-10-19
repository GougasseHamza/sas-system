import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sas_arena.models import Task, TaskKind
from sas_arena.providers.deepseek import DeepSeekProvider
from sas_arena.workspace_tools import WorkspaceTools


class WorkspaceToolTests(unittest.TestCase):
    def test_read_search_and_exact_edit_are_workspace_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "app.txt"
            source.write_text("alpha\nbeta\n")
            tools = WorkspaceTools(root, write_access=True)
            read = json.loads(tools.call("read_file", {"path": "app.txt"}))
            self.assertTrue(read["ok"])
            self.assertIn("alpha", read["data"]["content"])
            found = json.loads(
                tools.call("search_text", {"query": "beta", "pattern": "**/*"})
            )
            self.assertEqual(found["data"]["matches"][0]["line"], 2)
            edited = json.loads(
                tools.call(
                    "replace_text",
                    {"path": "app.txt", "old": "beta", "new": "gamma"},
                )
            )
            self.assertTrue(edited["ok"])
            self.assertIn("gamma", source.read_text())
            escaped = json.loads(tools.call("read_file", {"path": "../secret"}))
            self.assertFalse(escaped["ok"])

    def test_read_only_worker_has_no_edit_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            tools = WorkspaceTools(Path(temp), write_access=False)
            names = {item["function"]["name"] for item in tools.definitions()}
            self.assertNotIn("replace_text", names)
            denied = json.loads(
                tools.call(
                    "replace_text",
                    {"path": "x", "old": "a", "new": "b"},
                )
            )
            self.assertFalse(denied["ok"])


class FakeDeepSeek(DeepSeekProvider):
    def __init__(self, responses):
        super().__init__(model="test-model", timeout_seconds=10)
        self.responses = list(responses)
        self.requests = []

    def _request(self, body, api_key, timeout):
        self.requests.append(body)
        return self.responses.pop(0)


class DeepSeekToolLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_content_is_preserved_across_tool_turns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source.txt").write_text("evidence\n")
            final = json.dumps(
                {
                    "status": "completed",
                    "summary": "read evidence",
                    "confidence": 0.9,
                    "claims": [],
                    "artifacts": [],
                    "next_actions": [],
                }
            )
            provider = FakeDeepSeek(
                [
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "reasoning_content": "I should read the file.",
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "type": "function",
                                            "function": {
                                                "name": "read_file",
                                                "arguments": '{"path":"source.txt"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ],
                        "usage": {"total_tokens": 10},
                    },
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": final,
                                    "reasoning_content": "The evidence is present.",
                                    "tool_calls": None,
                                }
                            }
                        ],
                        "usage": {"total_tokens": 12},
                    },
                ]
            )
            task = Task("svc", TaskKind.SOURCE_MAP, "inspect source", str(root))
            schema = (
                Path(__file__).resolve().parents[1] / "schemas/agent_result.schema.json"
            )
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}):
                result = await provider.run(task, root, schema)
            self.assertEqual(result.provider, "deepseek")
            assistant = provider.requests[1]["messages"][2]
            self.assertEqual(assistant["reasoning_content"], "I should read the file.")
            self.assertEqual(result.usage["total_tokens"], 22)


if __name__ == "__main__":
    unittest.main()
