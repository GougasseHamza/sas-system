import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sas_arena.event_adapters import HttpEventAdapter


class EventAdapterTests(unittest.TestCase):
    def test_observation_and_submission_are_config_driven(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                value = (
                    {"game": {"tick": 17}}
                    if self.path == "/tick"
                    else {"teams": ["a", "b"]}
                    if self.path == "/score"
                    else {"service": {"a": "id-1"}}
                )
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):  # noqa: N802
                length = int(self.headers["Content-Length"])
                value = json.loads(self.rfile.read(length))
                body = json.dumps({"received": value["flag"]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "adapter.toml"
                config.write_text(
                    f"""
[http]
tick_url = "http://127.0.0.1:{port}/tick"
tick_path = "game.tick"
scoreboard_url = "http://127.0.0.1:{port}/score"
attack_info_url = "http://127.0.0.1:{port}/attack"
submit_url = "http://127.0.0.1:{port}/submit"
""".strip()
                )
                adapter = HttpEventAdapter.load(config)
                observed = adapter.observe()
                self.assertEqual(observed["tick"], 17)
                self.assertEqual(observed["scoreboard"]["teams"], ["a", "b"])
                submitted = adapter.submit("FLAG{X}")
                self.assertTrue(submitted["accepted"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_forcad_array_submission_checks_token_and_acceptance_message(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = json.dumps({"tick": 7}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_PUT(self):  # noqa: N802
                length = int(self.headers["Content-Length"])
                value = json.loads(self.rfile.read(length))
                assert self.headers["X-Team-Token"] == "test-token"
                assert value == ["A" * 31 + "="]
                body = json.dumps(
                    [{"flag": value[0], "msg": "Flag accepted! Earned points!"}]
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                del format, args

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        previous = os.environ.get("TEST_TEAM_TOKEN")
        os.environ["TEST_TEAM_TOKEN"] = "test-token"
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "adapter.toml"
                config.write_text(
                    f"""
[http]
tick_url = "http://127.0.0.1:{port}/tick"
tick_path = "tick"
submit_url = "http://127.0.0.1:{port}/flags"
submit_method = "PUT"
submit_payload = "array"
accepted_contains = "Flag accepted!"
token_env = "TEST_TEAM_TOKEN"
token_header = "X-Team-Token"
token_prefix = ""
""".strip()
                )
                result = HttpEventAdapter.load(config).submit("A" * 31 + "=")
                self.assertTrue(result["accepted"])
        finally:
            if previous is None:
                os.environ.pop("TEST_TEAM_TOKEN", None)
            else:
                os.environ["TEST_TEAM_TOKEN"] = previous
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
