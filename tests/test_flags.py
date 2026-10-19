import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from sas_arena.cells import shard_targets
from sas_arena.flags import FlagBroker
from sas_arena.hooks import HookRunner
from sas_arena.scope import ScopePolicy
from sas_arena.specs import EventSpec, HookSpec
from sas_arena.store import ArenaStore


class FlagBrokerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ArenaStore(self.root / "arena.db")
        self.store.initialize()
        self.event = EventSpec(
            "event",
            "event",
            self.root / "event.toml",
            {},
            flag_pattern=r"FLAG\{[^}]+\}",
            hooks={
                "submit": HookSpec(
                    (
                        sys.executable,
                        "-c",
                        "import json,sys; print(json.dumps({'accepted':sys.argv[1]}))",
                        "{flag}",
                    )
                )
            },
            submission_batch_size=2,
        )
        self.broker = FlagBroker(
            self.event,
            self.store,
            HookRunner(ScopePolicy((self.root.resolve(),))),
        )

    def tearDown(self):
        self.temp.cleanup()

    async def test_ndjson_ingestion_and_submission(self):
        output = "\n".join(
            [
                json.dumps({"flags": ["FLAG{one}", "invalid"]}),
                "fallback FLAG{two}",
            ]
        )
        flags = self.broker.extract(output)
        self.assertEqual(flags, ["FLAG{one}", "FLAG{two}"])
        self.assertEqual(self.broker.ingest("svc", "10.0.0.1", flags), 2)
        outcomes = await self.broker.submit_once()
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(outcome.passed for outcome in outcomes))

    async def test_batch_hook_submits_one_json_array(self):
        self.event = EventSpec(
            "event",
            "event",
            self.root / "event.toml",
            {},
            flag_pattern=r"FLAG\{[^}]+\}",
            hooks={
                "submit": HookSpec(
                    (
                        sys.executable,
                        "-c",
                        "import json,sys; flags=json.loads(sys.argv[1]); "
                        "print(json.dumps({'results':[{'flag':f,'accepted':True} "
                        "for f in flags]}))",
                        "{flags_json}",
                    )
                )
            },
            submission_batch_size=10,
        )
        broker = FlagBroker(
            self.event,
            self.store,
            HookRunner(ScopePolicy((self.root.resolve(),))),
        )
        flags = ["FLAG{one}", "FLAG{two}"]
        self.assertEqual(broker.ingest("svc", "10.0.0.1", flags), 2)
        outcomes = await broker.submit_once()
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].passed)
        self.assertEqual(self.store.service_snapshot("svc")["flags"]["submitted"], 2)

    def test_fair_leasing_and_expiry(self):
        now = time.time()
        entries = [
            ("FLAG{a1}", "svc-a", "10.0.0.1", now + 60),
            ("FLAG{a2}", "svc-a", "10.0.0.1", now + 60),
            ("FLAG{b1}", "svc-b", "10.0.0.2", now + 60),
            ("FLAG{old}", "svc-b", "10.0.0.2", now - 1),
        ]
        for flag, service, target, expiry in entries:
            self.store.record_flag(
                hashlib.sha256(flag.encode()).hexdigest(),
                service,
                target,
                flag,
                expires_at=expiry,
            )
        leased = self.store.lease_flags_fair("broker", limit=2, lease_seconds=30)
        self.assertEqual(
            {(item["service"], item["target"]) for item in leased},
            {("svc-a", "10.0.0.1"), ("svc-b", "10.0.0.2")},
        )
        second_lease = self.store.lease_flags_fair("other", limit=2, lease_seconds=30)
        self.assertEqual([item["flag"] for item in second_lease], ["FLAG{a2}"])
        self.assertFalse(
            {item["digest"] for item in leased}
            & {item["digest"] for item in second_lease}
        )
        self.assertEqual(
            self.store.lease_flags_fair("third", limit=2, lease_seconds=30), []
        )
        self.assertEqual(self.store.service_snapshot("svc-b")["flags"]["expired"], 1)

    def test_target_shards_are_disjoint_and_complete(self):
        targets = tuple(f"10.0.0.{index}" for index in range(1, 21))
        first = set(shard_targets("svc", targets, shard_index=0, shard_count=2))
        second = set(shard_targets("svc", targets, shard_index=1, shard_count=2))
        self.assertFalse(first & second)
        self.assertEqual(first | second, set(targets))


if __name__ == "__main__":
    unittest.main()
