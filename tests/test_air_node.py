import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "linux", "requires the PiLab Linux toolchain")
class AirNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("make") or not shutil.which("pkg-config"):
            raise unittest.SkipTest("requires make and pkg-config")
        if subprocess.run(["pkg-config", "--atleast-version=7.86.0", "libcurl"]).returncode:
            raise unittest.SkipTest("requires libcurl 7.86 development files")

        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        subprocess.run(["make", "-C", ROOT / "airpi", f"BUILD_DIR={directory.name}", "air-node"], check=True)
        cls.binary = Path(directory.name) / "air-node"

    def test_rejects_invalid_dust_before_connecting(self):
        for dust in ("nan", "inf", "-1", "1e18", "1e999", "23ppm", ""):
            with self.subTest(dust=dust):
                result = subprocess.run([self.binary, "not-a-url", dust], capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"dust", result.stderr)
                self.assertNotIn(b"WebSocket", result.stderr)

    def test_empty_stream_exits_without_connecting(self):
        result = subprocess.run([self.binary, "not-a-url", "--stdin"], input=b"", capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_rejects_malformed_climate_samples_before_connecting(self):
        for line in (b"23.5 24.1\n", b"23.5 nan 42\n", b"23.5 24.1 -1\n",
                     b"23.5 24.1 101\n", b"23.5 24.1 42 extra\n"):
            with self.subTest(line=line):
                result = subprocess.run([self.binary, "not-a-url", "--stdin"], input=line,
                                        capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"air-node:", result.stderr)
                self.assertNotIn(b"WebSocket", result.stderr)

    def test_rejects_overlong_and_nul_prefixed_stream_values(self):
        for line in (b"9" * 128 + b"\n", b"23.5\x00ignored\n"):
            with self.subTest(line=line):
                result = subprocess.run([self.binary, "not-a-url", "--stdin"], input=line,
                                        capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"invalid dust input line", result.stderr)
                self.assertNotIn(b"WebSocket", result.stderr)

    @contextmanager
    def recording_server(self, *, disconnect_first=False, opened=None, deny_reconnect=False, close_when=None,
                         accept_when=None):
        try:
            from websockets.exceptions import ConnectionClosed
            from websockets.sync.server import serve
        except ImportError:
            self.skipTest("requires lab/requirements.txt")

        messages = queue.Queue()
        closed = threading.Event()

        def record(connection):
            if opened is not None:
                opened.put(connection)
            try:
                for text in connection:
                    messages.put((connection, json.loads(text)))
                    if disconnect_first and not closed.is_set():
                        if close_when is not None:
                            close_when.wait(timeout=5)
                        connection.close()
                        return
            except ConnectionClosed:
                pass
            finally:
                closed.set()

        def check_handshake(connection, request):
            if accept_when is not None and not accept_when.wait(timeout=2.5):
                return connection.protocol.reject(HTTPStatus.SERVICE_UNAVAILABLE, "Test server offline")
            if deny_reconnect and closed.is_set():
                return connection.protocol.reject(HTTPStatus.SERVICE_UNAVAILABLE, "Test server offline")
            return None

        with serve(record, "127.0.0.1", 0, close_timeout=0.25, process_request=check_handshake) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            url = f"ws://127.0.0.1:{server.socket.getsockname()[1]}/socket"
            try:
                yield url, messages, closed
            finally:
                server.shutdown()
                thread.join(timeout=3)

    def test_sends_one_typed_dust_sample(self):
        with self.recording_server() as (url, messages, closed):
            subprocess.run([self.binary, url, "23.5"], check=True, timeout=10)
            self.assertTrue(closed.wait(timeout=3), "receiver did not observe the sender disconnecting")
            _, sample = messages.get_nowait()
            self.assert_sample(sample, 1, 23.5)
            self.assertTrue(messages.empty())

    def test_sends_two_lines_on_the_same_connection(self):
        with self.recording_server() as (url, messages, closed):
            subprocess.run([self.binary, url, "--stdin"], input=b"23.5\r\n41.2", check=True, timeout=10)
            self.assertTrue(closed.wait(timeout=3), "receiver did not observe the sender disconnecting")
            first_connection, first = messages.get_nowait()
            second_connection, second = messages.get_nowait()
            self.assertIs(first_connection, second_connection)
            self.assert_sample(first, 1, 23.5)
            self.assert_sample(second, 2, 41.2)
            self.assertEqual(first["runId"], second["runId"])
            self.assertTrue(messages.empty())

    def test_sends_temperature_and_humidity_in_the_same_sample_as_dust(self):
        with self.recording_server() as (url, messages, closed):
            subprocess.run([self.binary, url, "--stdin"],
                           input=b"23.5 24.1 42\n31 -5 100\n", check=True, timeout=10)
            self.assertTrue(closed.wait(timeout=3))
            connection, first = messages.get_nowait()
            second_connection, second = messages.get_nowait()
            self.assertIs(connection, second_connection)
            self.assert_sample(first, 1, 23.5, temperature=24.1, humidity=42)
            self.assert_sample(second, 2, 31, temperature=-5, humidity=100)
            self.assertEqual(first["runId"], second["runId"])
            self.assertTrue(messages.empty())

    def test_samples_room_every_three_seconds_even_when_value_stays_the_same(self):
        with tempfile.TemporaryDirectory() as directory, self.recording_server() as (url, messages, _):
            room = Path(directory) / "air.txt"
            room.write_text("23.5 24.1 42\n")
            with subprocess.Popen([self.binary, url, "--room-file", room]) as node:
                try:
                    start = time.monotonic()
                    _, first = messages.get(timeout=5)
                    first_at = time.monotonic()
                    self.assert_sample(first, 1, 23.5, temperature=24.1, humidity=42)
                    self.assertGreaterEqual(first_at - start, 2.7)
                    replacement = room.with_suffix(".next")
                    replacement.write_text("80 25 55\n")
                    replacement.replace(room)

                    _, second = messages.get(timeout=4)
                    second_at = time.monotonic()
                    self.assertAlmostEqual(second_at - first_at, 3, delta=0.5)
                    _, third = messages.get(timeout=4)
                    self.assertAlmostEqual(time.monotonic() - second_at, 3, delta=0.5)
                    self.assert_sample(second, 2, 80, temperature=25, humidity=55)
                    self.assert_sample(third, 3, 80, temperature=25, humidity=55)
                    self.assertEqual(first["runId"], third["runId"])
                finally:
                    node.terminate()

    def test_room_samples_while_connection_attempts_are_blocked(self):
        accept = threading.Event()
        with tempfile.TemporaryDirectory() as directory, self.recording_server(accept_when=accept) as (url, messages, _):
            room = Path(directory) / "air.txt"
            room.write_text("23.5 24.1 42\n")
            log = Path(directory) / "node.log"
            with log.open("w") as output, subprocess.Popen(
                [self.binary, url, "--room-file", room], stdout=output, stderr=subprocess.STDOUT,
            ) as node:
                try:
                    for tick in (1, 2):
                        deadline = time.monotonic() + 5
                        marker = f'"event":"room_sampled","tick":{tick}}}'
                        while marker not in log.read_text():
                            if time.monotonic() >= deadline:
                                self.fail(log.read_text())
                            time.sleep(0.03)
                        self.assertTrue(messages.empty())
                        if tick == 1:
                            replacement = room.with_suffix(".next")
                            replacement.write_text("80 25 55\n")
                            replacement.replace(room)

                    accept.set()
                    _, sample = messages.get(timeout=5)
                    self.assert_sample(sample, 1, 80, temperature=25, humidity=55)
                    self.assertIn('"event":"room_samples_replaced","count":1', log.read_text())
                finally:
                    accept.set()
                    node.terminate()

    def test_servo_commands_apply_during_room_sampling(self):
        opened = queue.Queue()
        with tempfile.TemporaryDirectory() as directory, self.recording_server(opened=opened) as (url, messages, _):
            room = Path(directory) / "air.txt"
            room.write_text("23.5 24.1 42\n")
            output = Path(directory) / "angle.txt"
            with subprocess.Popen([self.binary, url, "--room-file", room, "--servo-file", output]) as node:
                try:
                    connection = opened.get(timeout=4)
                    self.assertEqual(output.read_text(), "90\n")
                    _, state = messages.get(timeout=4)
                    self.assertEqual(state, {"type": "ServoStateReported", "nodeId": "airpi", "payload": {"angle": 90}})
                    command = {"type": "ServoCommand", "nodeId": "airpi",
                               "payload": {"commandId": "00000000-0000-0000-0000-000000000001", "angle": 0}}
                    invalid = {**command, "payload": {**command["payload"], "angle": 181}}
                    connection.send(json.dumps(invalid))
                    text = json.dumps(command)
                    connection.send([text[:19], text[19:]])
                    _, applied = messages.get(timeout=4)
                    self.assertEqual(applied, {**command, "type": "ServoCommandApplied"})
                    self.assertEqual(output.read_text(), "0\n")
                    _, sample = messages.get(timeout=5)
                    self.assert_sample(sample, 1, 23.5, temperature=24.1, humidity=42)

                    connection.close()
                    connection = opened.get(timeout=4)
                    _, state = messages.get(timeout=4)
                    self.assertEqual(state, {"type": "ServoStateReported", "nodeId": "airpi", "payload": {"angle": 0}})

                    output.unlink()
                    output.mkdir()
                    command["payload"] = {"commandId": "00000000-0000-0000-0000-000000000002", "angle": 90}
                    connection.send(json.dumps(command))
                    _, error = messages.get(timeout=4)
                    self.assertEqual(error, {**command, "type": "ServoCommandUnconfirmed"})
                    self.assertTrue(output.is_dir())
                    _, sample = messages.get(timeout=5)
                    self.assert_sample(sample, 2, 23.5, temperature=24.1, humidity=42)
                finally:
                    node.terminate()

    def test_new_process_gets_a_new_run_id_and_restarts_at_one(self):
        run_ids = set()
        for _ in range(2):
            with self.recording_server() as (url, messages, closed):
                subprocess.run([self.binary, url, "23.5"], check=True, timeout=10)
                self.assertTrue(closed.wait(timeout=3))
                _, sample = messages.get_nowait()
                self.assert_sample(sample, 1, 23.5)
                run_ids.add(sample["runId"])
        self.assertEqual(len(run_ids), 2)

    @contextmanager
    def air_stream(self, url):
        with subprocess.Popen([self.binary, url, "--stdin"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as node:
            try:
                yield node
            finally:
                if node.poll() is None:
                    node.kill()

    def send_dust(self, node, dust):
        node.stdin.write(f"{dust}\n")
        node.stdin.flush()

    def test_rejects_nul_before_end_of_input_line(self):
        with self.air_stream("not-a-url") as node:
            node.stdin.write("23.5\x00")
            node.stdin.flush()
            self.assertNotEqual(node.wait(timeout=5), 0)
            _, stderr = node.communicate(timeout=5)
            self.assertIn("invalid dust input line", stderr)

    def test_idle_reconnect_preserves_run_id_and_next_sequence(self):
        opened = queue.Queue()
        with self.recording_server(disconnect_first=True, opened=opened) as (url, messages, closed):
            with self.air_stream(url) as node:
                self.send_dust(node, 23.5)
                first_connection = opened.get(timeout=5)
                _, first = messages.get(timeout=5)
                self.assertTrue(closed.wait(timeout=5))

                replacement = opened.get(timeout=5)
                self.assertIsNot(first_connection, replacement)
                self.send_dust(node, 41.2)
                connection, fresh = messages.get(timeout=5)
                self.assertIs(connection, replacement)
                stdout, stderr = node.communicate(timeout=5)
                self.assertEqual(node.returncode, 0, stderr)
                events = [json.loads(line) for line in stdout.splitlines()]
                self.assertIn({"event": "reconnected"}, events)
                self.assertFalse(any(event.get("event") == "sensor_sample_abandoned" for event in events))
                self.assertTrue(messages.empty())

        self.assert_sample(first, 1, 23.5)
        self.assert_sample(fresh, 2, 41.2)
        self.assertEqual(first["runId"], fresh["runId"])

    def test_idle_reconnect_preserves_an_incomplete_input_line(self):
        opened = queue.Queue()
        close_when = threading.Event()
        with self.recording_server(disconnect_first=True, opened=opened, close_when=close_when) as (url, messages, closed):
            with self.air_stream(url) as node:
                self.send_dust(node, 23.5)
                first_connection = opened.get(timeout=5)
                _, first = messages.get(timeout=5)
                node.stdin.write("41.")
                node.stdin.flush()
                close_when.set()
                self.assertTrue(closed.wait(timeout=5))

                replacement = opened.get(timeout=5)
                self.assertIsNot(first_connection, replacement)
                self.assertTrue(messages.empty())
                self.send_dust(node, 2)
                _, fresh = messages.get(timeout=5)
                _, stderr = node.communicate(timeout=5)
                self.assertEqual(node.returncode, 0, stderr)

        self.assert_sample(fresh, 2, 41.2)
        self.assertEqual(first["runId"], fresh["runId"])

    def test_exits_after_three_failed_reconnect_attempts(self):
        with self.recording_server(disconnect_first=True, deny_reconnect=True) as (url, messages, closed):
            with self.air_stream(url) as node:
                self.send_dust(node, 23.5)
                _, first = messages.get(timeout=5)
                self.assertTrue(closed.wait(timeout=5))
                node.wait(timeout=10)
                stdout, stderr = node.communicate(timeout=10)
                self.assertNotEqual(node.returncode, 0, stderr)
                events = [json.loads(line) for line in stdout.splitlines()]
                attempts = [event for event in events if event.get("event") == "reconnect_attempt"]
                self.assertEqual([(event["attempt"], event["delay_ms"]) for event in attempts],
                                 [(1, 0), (2, 1000), (3, 2000)])
                self.assertIn({"event": "reconnect_exhausted"}, events)
                self.assertTrue(messages.empty())
        self.assert_sample(first, 1, 23.5)

    def assert_sample(self, sample, sequence, dust, **readings):
        run_id = sample.get("runId", "")
        self.assertEqual(sample, {
            "type": "SensorSample",
            "nodeId": "airpi",
            "runId": run_id,
            "payload": {"seq": sequence, "values": {"dust": dust, **readings}},
        })
        self.assertRegex(run_id, r"^[0-9a-f]{32}$")
