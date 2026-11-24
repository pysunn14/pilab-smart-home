import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_light_node(directory):
    wrapped = " ".join(f"-Wl,--wrap=light_gpio_{operation}" for operation in ("open", "close", "read", "set"))
    subprocess.run(
        ["make", "-C", ROOT / "lightpi", f"BUILD_DIR={directory}", f"LDFLAGS={wrapped}",
         "LDLIBS=../tests/light_gpio_stub.c", "light-node"], check=True,
    )
    return Path(directory) / "light-node"


def light_command(command_id, on):
    return json.dumps({"type": "LightCommand", "nodeId": "lightpi", "payload": {"commandId": command_id, "on": on}})


@unittest.skipUnless(sys.platform == "linux", "requires Linux GPIO and signalfd")
class LightNodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("pkg-config") is None:
            raise unittest.SkipTest("requires pkg-config")
        for package, version in (("libgpiod", "2.0"), ("libcurl", "8.14.0"), ("json-c", "0.15")):
            if subprocess.run(["pkg-config", f"--atleast-version={version}", package]).returncode != 0:
                raise unittest.SkipTest(f"requires {package} {version} development files")
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.binary = build_light_node(directory.name)

    @contextmanager
    def run_node(self, chip="test:ok", initial="off"):
        try:
            from websockets.sync.server import serve
        except ImportError:
            self.skipTest("requires lab/requirements.txt")

        sockets = queue.Queue()
        finished = threading.Event()

        def accept(connection):
            sockets.put(connection)
            finished.wait(timeout=10)

        with serve(accept, "127.0.0.1", 0, close_timeout=0.5) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            port = server.socket.getsockname()[1]
            with subprocess.Popen([self.binary, chip, "18", initial, f"ws://127.0.0.1:{port}/socket"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as node:
                try:
                    yield node, sockets.get(timeout=5)
                finally:
                    if node.poll() is None:
                        node.kill()
                    node.communicate(timeout=3)
                    finished.set()
                    server.shutdown()
                    thread.join(timeout=3)

    def test_fragmented_command_writes_gpio_before_echoing_id(self):
        with self.run_node(initial="on") as (node, connection):
            report = json.loads(connection.recv(timeout=3))
            self.assertRegex(report.pop("runId"), r"^[0-9a-f]{32}$")
            self.assertEqual(report,
                             {"type": "LightStateReported", "nodeId": "lightpi", "payload": {"on": True}})
            command_id = 'mainpi/"run"/1'
            frame = light_command(command_id, False)
            connection.send([frame[:29], frame[29:]])
            self.assertEqual(json.loads(connection.recv(timeout=3)), {
                "type": "LightCommandApplied", "nodeId": "lightpi", "payload": {"commandId": command_id, "on": False},
            })
            connection.send(json.dumps({
                "type": "LightStateQuery", "nodeId": "lightpi", "payload": {"queryId": "after-fragment"},
            }))
            self.assertEqual(json.loads(connection.recv(timeout=3)), {
                "type": "LightStateSnapshot", "nodeId": "lightpi",
                "payload": {"queryId": "after-fragment", "lastAppliedCommandId": command_id, "on": False},
            })

            node.terminate()
            stdout, stderr = node.communicate(timeout=3)
            self.assertEqual(node.returncode, 0, stderr)
            gpio_writes = [event for event in map(json.loads, stdout.splitlines()) if event["event"] == "gpio"]
            self.assertEqual(gpio_writes, [{"event": "gpio", "offset": 18, "on": False}])

    def test_write_error_closes_connection_without_applied_reply(self):
        with self.run_node(chip="test:fail") as (node, connection):
            from websockets.exceptions import ConnectionClosed

            connection.recv(timeout=3)
            connection.send(light_command("mainpi/run/1", True))
            with self.assertRaises(ConnectionClosed):
                connection.recv(timeout=3)
            stdout, stderr = node.communicate(timeout=3)
            self.assertEqual(node.returncode, 1, stderr)
            self.assertIn("apply light command", stderr)
            self.assertNotIn('"event":"gpio"', stdout)

    def test_repeated_id_returns_two_replies_for_one_gpio_write(self):
        with self.run_node() as (node, connection):
            connection.recv(timeout=3)
            command_id = "mainpi/run/1"
            expected = {"type": "LightCommandApplied", "nodeId": "lightpi",
                        "payload": {"commandId": command_id, "on": True}}
            for _ in range(2):
                connection.send(light_command(command_id, True))
                self.assertEqual(json.loads(connection.recv(timeout=3)), expected)

            node.terminate()
            stdout, stderr = node.communicate(timeout=3)
            self.assertEqual(node.returncode, 0, stderr)
            gpio_writes = [event for event in map(json.loads, stdout.splitlines()) if event["event"] == "gpio"]
            self.assertEqual(gpio_writes, [{"event": "gpio", "offset": 18, "on": True}])

    @unittest.skipUnless(os.environ.get("PILAB_TEST_SPRING") == "1", "enable the Spring integration test")
    def test_spring_commands_change_gpio_and_receive_matching_replies(self):
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.skipTest("requires lab/requirements.txt")
        from lab.spring_receiver import stage_receiver

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "spring"
            stage_receiver(ROOT, build)
            subprocess.run(["./gradlew", "--no-daemon", "--console=plain", "bootJar"],
                           cwd=build, check=True, timeout=240)
            [jar] = (build / "build" / "libs").glob("*.jar")
            log = root / "spring.log"
            with log.open("wb") as output, subprocess.Popen(
                    ["java", "-jar", jar, "--server.port=0", "--server.address=127.0.0.1",
                     "--PILAB_LIGHT_IP=127.0.0.1", "--pilab.control.light.queue-capacity=2"],
                    stdout=output, stderr=subprocess.STDOUT) as spring:
                try:
                    port = int(self.wait_for_log(spring, log, r"Tomcat started on port (\d+)").group(1))
                    self.verify_spring_commands(spring, log, port, connect)
                finally:
                    spring.terminate()
                    try:
                        spring.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        spring.kill()

    def verify_spring_commands(self, spring, log, port, connect):
        run_id = "0123456789abcdef0123456789abcdef"
        url = f"ws://127.0.0.1:{port}/socket"
        with subprocess.Popen([self.binary, "test:ok", "18", "off", url],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as node:
            try:
                self.wait_for_log(spring, log, r"light_state_reported on=false result=INITIALIZED")
                # Separate loopback addresses model the two Pi nodes in Spring's session registry.
                with socket.create_connection(("127.0.0.1", port), timeout=3,
                                              source_address=("127.0.0.11", 0)) as sock, connect(
                        url, sock=sock, open_timeout=3, close_timeout=1) as main:
                    for sequence, on in ((1, "true"), (2, "false")):
                        main.send(json.dumps({"type": "ButtonPressed", "nodeId": "mainpi", "runId": run_id,
                                              "payload": {"seq": sequence}}))
                        self.wait_for_log(spring, log,
                                          rf'light_command_applied commandId="mainpi/{run_id}/{sequence}" '
                                          rf"on={on} result=CONFIRMED")
                node.terminate()
                stdout, stderr = node.communicate(timeout=3)
                self.assertEqual(node.returncode, 0, stderr)
                events = (json.loads(line) for line in stdout.splitlines())
                self.assertEqual([event for event in events if event["event"] == "gpio"], [
                    {"event": "gpio", "offset": 18, "on": True},
                    {"event": "gpio", "offset": 18, "on": False},
                ])
            finally:
                if node.poll() is None:
                    node.kill()

    def wait_for_log(self, process, log, pattern):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            match = re.search(pattern, log.read_text(encoding="utf-8"))
            if match:
                return match
            time.sleep(0.05)
        self.fail(f"Spring did not report {pattern!r}\n{log.read_text(encoding='utf-8')[-3500:]}")
