import json
import os
import queue
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BUTTON_PRESSED_EVENT = {"event": "button_pressed", "type": "ButtonPressed"}
RUN_ID = r"[0-9a-f]{32}"


@unittest.skipUnless(sys.platform == "linux", "requires Linux signalfd")
class ButtonMonitorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("pkg-config") is None:
            raise unittest.SkipTest("requires pkg-config")
        for package, version in (("libgpiod", "2.0"), ("libcurl", "7.86.0")):
            if subprocess.run(
                ["pkg-config", f"--atleast-version={version}", package], check=False
            ).returncode != 0:
                raise unittest.SkipTest(f"requires {package} {version} development files")

        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.binary = Path(directory.name) / "button-monitor"
        wrapped = " ".join(
            f"-Wl,--wrap=button_gpio_{operation}"
            for operation in ("open", "close", "fd", "read_level", "read_edge")
        )
        subprocess.run(
            ["make", "-C", ROOT / "mainpi", f"BUILD_DIR={directory.name}",
             "CPPFLAGS=-DBUTTON_MONITOR_HEARTBEAT_NS=200000000",
             f"LDFLAGS={wrapped}", "LDLIBS=../tests/button_monitor_gpio_stub.c",
             "button-monitor"],
            check=True,
        )

    def start_monitor(self, initially_high, queued_edge=None, websocket_url=None):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, write_fd)
        if queued_edge is not None:
            self.write_edge(write_fd, queued_edge)
        arguments = [self.binary, "pipe:high" if initially_high else "pipe:low", "20", "10"]
        if websocket_url is not None:
            arguments.append(websocket_url)
        try:
            process = subprocess.Popen(
                arguments,
                pass_fds=(read_fd,), env=dict(os.environ, PILAB_TEST_EDGE_FD=str(read_fd)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            )
        finally:
            os.close(read_fd)
        self.addCleanup(self.reap_monitor, process)
        return process, write_fd

    @staticmethod
    def reap_monitor(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)

    @staticmethod
    def write_edge(fd, high):
        edge_ns = time.monotonic_ns()
        os.write(fd, struct.pack("=QB", edge_ns, high))
        return edge_ns

    def assert_press(self, process, sequence, edge_ns=None):
        record = self.next_record(process)
        observed_edge = record.pop("edge_ns")
        if edge_ns is None:
            self.assertGreater(observed_edge, 0)
        else:
            self.assertEqual(observed_edge, edge_ns)
        self.assertEqual(record, dict(BUTTON_PRESSED_EVENT, seq=sequence))

    def next_record(self, process, *, include_heartbeat=False):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            remaining = max(0, deadline - time.monotonic())
            self.assertTrue(select.select([process.stdout], [], [], remaining)[0],
                            "monitor did not log")
            line = process.stdout.readline()
            self.assertTrue(line, f"monitor exited with status {process.poll()}")
            record = json.loads(line)
            if include_heartbeat or record["event"] != "heartbeat":
                return record
        self.fail("monitor only logged heartbeats")

    def stop_monitor(self, process):
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, stderr)
        records = map(json.loads, stdout.splitlines())
        return [record for record in records if record["event"] != "heartbeat"]

    def send_two_websocket_presses(self, process, write_fd):
        self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": True})
        for sequence in (1, 2):
            self.assert_press(process, sequence, self.write_edge(write_fd, False))
            self.assertEqual(self.next_record(process), {"event": "button_tx_dequeued", "seq": sequence})
            self.assertEqual(self.next_record(process), {"event": "button_tx_sent", "seq": sequence})
            if sequence == 1:
                self.write_edge(write_fd, True)
                self.assertEqual(self.next_record(process), {"event": "button_released"})

    def test_held_press_is_logged_once_then_sigint_stops_the_monitor(self):
        process, write_fd = self.start_monitor(initially_high=True)
        self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": True})
        self.assert_press(process, 1, self.write_edge(write_fd, False))
        self.assertEqual(self.stop_monitor(process),
                         [{"event": "button_tx_dequeued", "seq": 1}, {"event": "stopped"}])

    def test_two_presses_emit_two_button_events_in_order(self):
        process, write_fd = self.start_monitor(initially_high=True)
        self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": True})
        self.assert_press(process, 1, self.write_edge(write_fd, False))
        self.assertEqual(self.next_record(process), {"event": "button_tx_dequeued", "seq": 1})
        self.write_edge(write_fd, True)
        self.assertEqual(self.next_record(process), {"event": "button_released"})
        self.assert_press(process, 2, self.write_edge(write_fd, False))
        self.assertEqual(self.stop_monitor(process),
                         [{"event": "button_tx_dequeued", "seq": 2}, {"event": "stopped"}])

    def test_held_at_startup_does_not_log_a_press(self):
        process, write_fd = self.start_monitor(initially_high=False)
        self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": False})
        self.write_edge(write_fd, True)
        self.assertEqual(self.next_record(process), {"event": "button_released"})
        self.assertEqual(self.stop_monitor(process), [{"event": "stopped"}])

    def test_edge_queued_during_setup_uses_its_previous_level(self):
        process, _ = self.start_monitor(initially_high=False, queued_edge=False)
        self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": False})
        self.assert_press(process, 1)
        self.assertEqual(self.stop_monitor(process),
                         [{"event": "button_tx_dequeued", "seq": 1}, {"event": "stopped"}])

    def test_idle_monitor_reports_its_heartbeat(self):
        process, _ = self.start_monitor(initially_high=True)
        self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": True})
        self.assertEqual(len(list(Path(f"/proc/{process.pid}/task").iterdir())), 2)
        self.assertEqual(self.next_record(process, include_heartbeat=True), {"event": "heartbeat"})
        self.assertEqual(self.stop_monitor(process), [{"event": "stopped"}])

    def test_websocket_button_id_uses_one_run_id_per_process(self):
        try:
            from websockets.exceptions import ConnectionClosed
            from websockets.sync.server import serve
        except ImportError:
            self.skipTest("requires websockets for the test server")

        received = queue.Queue()
        connected = queue.Queue()

        def receive_presses(connection):
            connected.put(connection.request.path)
            messages = [json.loads(connection.recv(timeout=3)) for _ in range(2)]
            received.put(messages)
            try:
                connection.recv(timeout=3)
            except ConnectionClosed:
                pass

        with serve(receive_presses, "127.0.0.1", 0, close_timeout=0.5) as server:
            port = server.socket.getsockname()[1]
            server_thread = threading.Thread(target=server.serve_forever)
            server_thread.start()
            try:
                run_ids = []
                for _ in range(2):
                    process, write_fd = self.start_monitor(
                        initially_high=True, websocket_url=f"ws://127.0.0.1:{port}/socket"
                    )
                    self.assertEqual(connected.get(timeout=3), "/socket")
                    self.send_two_websocket_presses(process, write_fd)
                    messages = received.get(timeout=3)
                    run_id = messages[0]["runId"]
                    self.assertRegex(run_id, f"^{RUN_ID}$")
                    self.assertEqual(messages, [
                        {"type": "ButtonPressed", "nodeId": "mainpi", "runId": run_id,
                         "payload": {"seq": sequence}} for sequence in (1, 2)
                    ])
                    run_ids.append(run_id)
                    self.assertEqual(self.stop_monitor(process), [{"event": "stopped"}])
                self.assertNotEqual(*run_ids)
            finally:
                server.shutdown()
                server_thread.join(timeout=3)

    def test_websocket_connection_failure_reports_the_affected_press(self):
        with socket.socket() as unserved:
            unserved.bind(("127.0.0.1", 0))
            port = unserved.getsockname()[1]
            process, write_fd = self.start_monitor(
                initially_high=True, websocket_url=f"ws://127.0.0.1:{port}/socket"
            )
            self.assertEqual(self.next_record(process), {"event": "ready", "gpio_high": True})
            self.write_edge(write_fd, False)
            stdout, stderr = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 1, stderr)
        self.assertIn(b"WebSocket connect failed", stderr)
        self.assertIn(b"WebSocket event unsent for seq 1", stderr)
        self.assertNotIn(b"button_tx_sent", stdout)

    @unittest.skipUnless(os.environ.get("PILAB_TEST_SPRING") == "1", "enable the Spring integration test")
    def test_spring_receives_typed_button_events_and_flat_sensor_data(self):
        from lab.spring_receiver import stage_receiver
        from websockets.sync.client import connect

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "spring"
            stage_receiver(ROOT, build)
            subprocess.run(["./gradlew", "--no-daemon", "--console=plain", "bootJar"],
                           cwd=build, check=True, timeout=240)
            [jar] = (build / "build" / "libs").glob("*.jar")
            log = root / "spring.log"
            env = dict(os.environ, PILAB_MAIN_IP="127.0.0.11", PILAB_FIRE_IP="127.0.0.12",
                       PILAB_AIR_IP="127.0.0.13", PILAB_LIGHT_IP="127.0.0.14")
            with log.open("wb") as output:
                spring = subprocess.Popen(
                    ["java", "-jar", jar, "--server.port=0", "--server.address=127.0.0.1"],
                    env=env, stdout=output, stderr=subprocess.STDOUT,
                )
            try:
                port = self.wait_for_spring_log(spring, log, r"Tomcat started on port (\d+)")
                url = f"ws://127.0.0.1:{port}/socket"
                process, write_fd = self.start_monitor(initially_high=True, websocket_url=url)
                self.send_two_websocket_presses(process, write_fd)
                self.assertEqual(self.stop_monitor(process), [{"event": "stopped"}])
                receipt = rf'button_received nodeId="mainpi" runId=({RUN_ID}) seq='
                run_id = self.wait_for_spring_log(spring, log, receipt + r"2$")
                largest_sequence = (1 << 64) - 1

                with connect(url, open_timeout=3, close_timeout=1) as connection:
                    connection.send(json.dumps({"dust": 23.5}))
                    connection.send(json.dumps({
                        "type": "ButtonPressed", "nodeId": "mainpi", "payload": {"seq": 3},
                    }))
                    self.assertEqual(json.loads(connection.recv(timeout=3)), {
                        "status": "error", "message": "Invalid ButtonPressed runId",
                    })
                    connection.send(json.dumps({
                        "type": "ButtonPressed", "nodeId": "mainpi", "runId": run_id,
                        "payload": {"seq": "3"},
                    }))
                    self.assertEqual(json.loads(connection.recv(timeout=3)), {
                        "status": "error", "message": "Invalid ButtonPressed sequence",
                    })
                    connection.send(json.dumps({
                        "type": "ButtonPressed", "nodeId": "mainpi", "runId": run_id,
                        "payload": {"seq": largest_sequence},
                    }))
                    self.wait_for_spring_log(spring, log, receipt + rf"{largest_sequence}$")
                    with urlopen(f"http://127.0.0.1:{port}/home/data", timeout=3) as response:
                        self.assertEqual(json.load(response)["dust"], 23.5)

                receipts = re.findall(receipt + r"(\d+)$", log.read_text(encoding="utf-8"), re.MULTILINE)
                self.assertEqual(receipts, [(run_id, n) for n in ("1", "2", str(largest_sequence))])
            finally:
                spring.terminate()
                try:
                    spring.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    spring.kill()
                    spring.wait(timeout=3)

    def wait_for_spring_log(self, process, log, pattern):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and process.poll() is None:
            match = re.search(pattern, log.read_text(encoding="utf-8"), re.MULTILINE)
            if match:
                return match.group(1)
            time.sleep(0.05)
        self.fail(f"Spring did not report {pattern!r}\n{log.read_text(encoding='utf-8')[-5000:]}")
