import unittest

from sas_arena.models import Risk, Task, TaskKind
from sas_arena.router import LatencyRouter


class RouterTests(unittest.TestCase):
    def test_high_value_hard_task_hedges_codex(self):
        task = Task(
            "service-a",
            TaskKind.EXPLOIT_BUILD,
            "build exploit",
            ".",
            expected_value=0.9,
        )
        plan = LatencyRouter(45).plan(task, {"deepseek", "claude", "codex"})
        self.assertEqual(plan.immediate, ("deepseek", "claude"))
        self.assertEqual(plan.hedged, ("codex",))
        self.assertEqual(plan.hedge_delay_seconds, 45)

    def test_critical_task_starts_codex_immediately(self):
        task = Task(
            "service-b",
            TaskKind.DIAGNOSE,
            "analyze verifier disagreement",
            ".",
            risk=Risk.CRITICAL,
        )
        plan = LatencyRouter().plan(task, {"deepseek", "claude", "codex"})
        self.assertIn("codex", plan.immediate)
        self.assertFalse(plan.hedged)

    def test_named_accounts_are_routed_by_provider_family(self):
        task = Task(
            "service-a",
            TaskKind.DIAGNOSE,
            "resolve conflicting evidence",
            ".",
            risk=Risk.CRITICAL,
        )
        plan = LatencyRouter().plan(
            task,
            {
                "codex-a": "codex",
                "codex-b": "codex",
                "claude-a": "claude",
                "deepseek-a": "deepseek",
            },
        )
        self.assertTrue({"codex-a", "codex-b"} & set(plan.immediate))
        self.assertIn("claude-a", plan.immediate)
        self.assertIn("deepseek-a", plan.immediate)

    def test_family_preference_resolves_to_named_account(self):
        task = Task(
            "service-a",
            TaskKind.DIAGNOSE,
            "deep review",
            ".",
            preferred_provider="codex",
        )
        plan = LatencyRouter().plan(task, {"codex-b": "codex", "claude-a": "claude"})
        self.assertIn(plan.immediate, (("codex-b",),))

    def test_claude_operator_falls_back_to_codex_when_cli_is_missing(self):
        task = Task(
            "service-a",
            TaskKind.DIAGNOSE,
            "operate service",
            ".",
            preferred_provider="claude",
            context={"provider_fallbacks": ["codex"]},
        )
        plan = LatencyRouter().plan(
            task, {"codex-b": "codex", "deepseek-a": "deepseek"}
        )
        self.assertEqual(plan.immediate, ("codex-b",))


if __name__ == "__main__":
    unittest.main()
