import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sas_arena.cli import _provider_enabled
from sas_arena.models import Task, TaskKind
from sas_arena.providers.claude_cli import ClaudeCliProvider
from sas_arena.providers.claude_cli import _claude_progress
from sas_arena.providers.opencode_cli import (
    _opencode_progress,
    _structured_payload,
)


class FullAgentHarnessTests(unittest.TestCase):
    def test_claude_default_has_no_arena_turn_semaphore(self):
        provider = ClaudeCliProvider()
        self.assertEqual(provider.max_concurrency, 0)
        self.assertIsNone(provider._semaphore)

    def test_claude_operator_receives_scoped_dispatch_and_control_mcp(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "arena.db"
            provider = ClaudeCliProvider(
                dispatch_database=database,
                control_database=database,
                dispatch_max_children=9,
            )
            task = Task("svc", TaskKind.DIAGNOSE, "operate service", temp)
            config = json.loads(provider._mcp_config(task) or "{}")
            servers = config["mcpServers"]
            self.assertIn("sas_dispatch", servers)
            self.assertIn("sas_control", servers)
            self.assertIn(task.id, servers["sas_control"]["args"])
            self.assertIn("9", servers["sas_dispatch"]["args"])

    def test_optional_missing_harness_is_skipped_and_required_fails(self):
        values = {
            "family": "claude",
            "command": "/definitely/missing/claude",
            "availability": "optional",
        }
        with patch("sas_arena.cli.resolve_executable", return_value=None):
            self.assertFalse(_provider_enabled("claude-away", values))
            values["availability"] = "required"
            with self.assertRaisesRegex(RuntimeError, "required provider"):
                _provider_enabled("claude-away", values)

    def test_opencode_extracts_structured_result_from_json_events(self):
        payload = {
            "status": "completed",
            "summary": "proved locally",
            "confidence": 0.9,
            "claims": [],
            "artifacts": [],
            "next_actions": [],
        }
        events = [
            {"type": "step-start", "sessionID": "session-1"},
            {"type": "text", "part": {"type": "text", "text": str(payload)}},
            {"type": "result", "structured_output": payload},
        ]
        self.assertEqual(_structured_payload(events), payload)

    def test_opencode_progress_exposes_tools_but_not_reasoning(self):
        tool = {
            "type": "tool",
            "part": {
                "type": "tool",
                "tool": "bash",
                "state": {"status": "running", "input": {"command": "pytest"}},
            },
        }
        self.assertEqual(_opencode_progress(tool), "tool bash running")
        self.assertIsNone(
            _opencode_progress(
                {"type": "reasoning", "part": {"type": "reasoning", "text": "hidden"}}
            )
        )

    def test_claude_progress_exposes_tool_name_but_not_thinking(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "private"},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "pytest"},
                    },
                ]
            },
        }
        self.assertEqual(_claude_progress(event), "tool Bash started")


if __name__ == "__main__":
    unittest.main()
