import json
import tempfile
import unittest
from pathlib import Path

from sas_arena.traffic_adapters import normalize_file


class TrafficAdapterTests(unittest.TestCase):
    def test_suricata_alerts_are_filtered_clustered_and_incremental(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            eve = root / "eve.json"
            cursor = root / "cursor.json"
            records = [
                {
                    "timestamp": "t1",
                    "event_type": "alert",
                    "src_ip": "10.0.0.1",
                    "dest_ip": "10.0.0.2",
                    "dest_port": 8080,
                    "proto": "TCP",
                    "alert": {"signature_id": 7, "signature": "Exploit", "severity": 1},
                },
                {
                    "timestamp": "t2",
                    "event_type": "alert",
                    "src_ip": "10.0.0.1",
                    "dest_ip": "10.0.0.2",
                    "dest_port": 8080,
                    "proto": "TCP",
                    "alert": {"signature_id": 7, "signature": "Exploit", "severity": 1},
                },
                {
                    "timestamp": "t3",
                    "event_type": "alert",
                    "src_ip": "10.99.0.5",
                    "alert": {"signature": "Checker", "severity": 1},
                },
            ]
            eve.write_text("".join(json.dumps(item) + "\n" for item in records))
            first = normalize_file(
                eve,
                source_format="suricata",
                cursor_path=cursor,
                checker_cidrs=("10.99.0.0/16",),
            )
            self.assertEqual(len(first["detections"]), 1)
            self.assertEqual(first["detections"][0]["count"], 2)
            second = normalize_file(
                eve,
                source_format="suricata",
                cursor_path=cursor,
                checker_cidrs=("10.99.0.0/16",),
            )
            self.assertEqual(second["detections"], [])


if __name__ == "__main__":
    unittest.main()
