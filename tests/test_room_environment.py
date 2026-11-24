import json
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from lab.control_web import dashboard_handler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALUES = {"dust": 23.5, "temperature": 24.1, "humidity": 42}


class RoomEnvironmentTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.room_file = Path(directory.name) / "air.txt"
        self.blind_requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_handler(
            ROOT / "web", room_file=self.room_file, move_blind=self.move_blind,
        ))
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"

    def move_blind(self, direction):
        self.blind_requests.append(direction)
        status = "applied" if direction == "UP" else "unconfirmed"
        return 200, {"commandId": "test-command", "direction": direction, "status": status}

    def test_blind_commands_preserve_receipt_and_require_same_origin(self):
        path = self.url + "/api/lab/blind"
        with urlopen(path, timeout=4) as response:
            self.assertEqual(json.load(response), {"enabled": True})

        def post(direction, *, origin=None, body=b"{}"):
            return urlopen(Request(path + "/" + direction, data=body, headers={
                "Origin": origin or self.url, "Content-Type": "application/json",
            }), timeout=4)

        for direction, origin, body, status in (
            ("UP", "http://example.test", b"{}", 403),
            ("UP", self.url, b"[]", 400),
            ("LEFT", self.url, b"{}", 404),
        ):
            with self.assertRaises(HTTPError) as caught:
                post(direction, origin=origin, body=body)
            self.assertEqual(caught.exception.code, status)
            caught.exception.close()
        self.assertEqual(self.blind_requests, [])
        with post("UP") as response:
            self.assertEqual(json.load(response),
                             {"commandId": "test-command", "direction": "UP", "status": "applied"})
        with post("DOWN") as response:
            self.assertEqual(json.load(response)["status"], "unconfirmed")
        self.assertEqual(self.blind_requests, ["UP", "DOWN"])

    def api(self, values=None, *, origin=None, content_type="application/json", node="air"):
        data = json.dumps(values).encode() if values is not None else None
        headers = {"Content-Type": content_type, "Origin": origin or self.url} if data else {}
        path = "/api/environment" if node == "air" else f"/api/environment/{node}"
        with urlopen(Request(self.url + path, data=data, headers=headers), timeout=4) as response:
            return json.load(response)

    def cli(self, *args):
        return subprocess.run(
            [str(ROOT / "pilab"), "lab", "environment", *args, "--url", self.url],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout

    def test_web_and_cli_share_the_room_and_sample_format(self):
        self.assertEqual(self.api(), {"values": DEFAULT_VALUES, "revision": 0})
        edit = self.api({"temperature": -2.5})
        self.assertEqual(json.loads(self.cli("get")), edit)
        self.assertEqual(self.cli("sample"), "23.5 -2.5 42\n")
        edit = json.loads(self.cli("set", "--humidity", "53", "--dust", "80"))
        self.assertEqual(edit, {"values": {"dust": 80, "temperature": -2.5, "humidity": 53}, "revision": 2})
        self.assertEqual(self.api({"humidity": 53}), edit)
        self.assertEqual(self.api(), edit)

    def test_invalid_patch_cannot_apply_any_values(self):
        for values in ({}, [], {"dust": 99, "humidity": 101}, {"dust": -1}, {"temperature": True},
                       {"dust": "23"}, {"temperature": float("inf")}, {"dust": 10**50}, {"gas": 30},
                       {"dust": 92233720368547759}, {"temperature": 1e-309},
                       {"dust": "0" * 4096}):
            with self.subTest(values=str(values)[:80]), self.assertRaises(HTTPError) as result:
                self.api(values)
            self.assertEqual(result.exception.code, 400)
            result.exception.close()
        self.assertEqual(self.api(), {"values": DEFAULT_VALUES, "revision": 0})

    def test_write_requires_same_origin_and_json(self):
        for options, status in (({"origin": "http://example.test"}, 403), ({"content_type": "text/plain"}, 400)):
            with self.assertRaises(HTTPError) as result:
                self.api({"dust": 50}, **options)
            self.assertEqual(result.exception.code, status)
            result.exception.close()
        self.assertEqual(self.api()["revision"], 0)

    def test_concurrent_partial_changes_keep_both_values(self):
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.api, [{"dust": 55}, {"humidity": 72}]))
        self.assertEqual(sorted(result["revision"] for result in results), [1, 2])
        self.assertEqual(self.api(), {"values": {"dust": 55, "temperature": 24.1, "humidity": 72}, "revision": 2})

    def test_room_file_changes_as_a_complete_snapshot(self):
        with self.room_file.open() as previous:
            self.api({"dust": 80, "humidity": 55})
            self.assertEqual(previous.read(), "23.5 24.1 42\n")
        self.assertEqual(self.room_file.read_text(), "80 24.1 55\n")

    def test_failed_file_replacement_leaves_web_state_unchanged(self):
        self.room_file.unlink()
        self.room_file.mkdir()
        with self.assertRaises(HTTPError) as result:
            self.api({"dust": 80})
        self.assertEqual(result.exception.code, 500)
        result.exception.close()
        self.assertEqual(self.api(), {"values": DEFAULT_VALUES, "revision": 0})

    def test_web_server_refuses_to_replace_existing_room_file_on_startup(self):
        with self.assertRaises(FileExistsError):
            dashboard_handler(ROOT / "web", room_file=self.room_file)

    def test_fire_inputs_are_shared_with_cli_and_keep_air_inputs_unchanged(self):
        self.assertEqual(self.api(node="fire"), {"values": {"infra": 1000, "gas": 10}, "revision": 0})
        edit = json.loads(self.cli("set", "--node", "fire", "--infra", "812", "--gas", "40"))
        self.assertEqual(edit, {"values": {"infra": 812, "gas": 40}, "revision": 1})
        self.assertEqual(self.cli("sample", "--node", "fire"), "812 40\n")
        self.assertEqual(self.api({"gas": 40}, node="fire"), edit)
        self.assertEqual(self.api(), {"values": DEFAULT_VALUES, "revision": 0})
        self.assertEqual(self.room_file.read_text(), "23.5 24.1 42\n")

    def test_invalid_fire_change_is_rejected_atomically(self):
        for patch in ({"infra": 812, "gas": -1}, {"infra": "812"}, {"temperature": 90}):
            with self.assertRaises(HTTPError) as result:
                self.api(patch, node="fire")
            self.assertEqual(result.exception.code, 400)
            result.exception.close()
        self.assertEqual(self.api(node="fire")["revision"], 0)

    def test_light_input_keeps_the_other_devices_unchanged(self):
        self.assertEqual(self.api(node="light"), {"values": {"light": 500}, "revision": 0})
        change = self.api({"light": 180}, node="light")
        self.assertEqual(change, {"values": {"light": 180}, "revision": 1})
        self.assertEqual(self.cli("sample", "--node", "light"), "180\n")
        via_cli = json.loads(self.cli("set", "--node", "light", "--light", "800"))
        self.assertEqual(via_cli, {"values": {"light": 800}, "revision": 2})
        with self.assertRaises(HTTPError) as result:
            self.api({"light": -1}, node="light")
        self.assertEqual(result.exception.code, 400)
        result.exception.close()
        self.assertEqual(self.api(node="light"), via_cli)
        self.assertEqual(self.api(node="fire")["revision"], 0)
        self.assertEqual(self.api()["revision"], 0)
