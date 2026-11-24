import contextlib
import json
import subprocess
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

from lab.control_web import allowed_request
from lab.session import ExperimentSession, SessionConflict


class ControlOriginTest(unittest.TestCase):
    def test_control_requires_direct_loopback_or_the_configured_proxy_identity(self):
        port = 19467
        origin = f"http://lab-host:{port}"
        owner = "owner@example.test"

        def allowed(host, **extra):
            headers = Message()
            headers["Host"] = host
            for name, value in extra.items():
                headers[name] = value
            return allowed_request(headers, port, origin, owner)

        loopback = f"127.0.0.1:{port}"
        self.assertEqual(allowed(loopback), f"http://{loopback}")
        self.assertIsNone(allowed(loopback, **{"X-Forwarded-For": "192.0.2.20"}))
        self.assertIsNone(allowed(f"lab-host:{port}"))
        self.assertIsNone(allowed(f"lab-host:{port}", **{"Tailscale-User-Login": "another@example.test"}))
        self.assertEqual(allowed(f"lab-host:{port}", **{"Tailscale-User-Login": owner}), origin)


class SessionTest(unittest.TestCase):
    def test_receiver_mode_records_server_sequence_for_two_presses(self):
        @contextlib.contextmanager
        def opened(repo, output, prefix, profile, on_progress):
            self.assertEqual(profile, "button-receiver")
            output.mkdir()
            yield object(), {"led": None, "connected": []}

        def press(lab, sequence, observe):
            observe("INPUT", {"value": 0, "timestamp_ns": sequence * 1000})
            return sequence

        with tempfile.TemporaryDirectory() as path, patch("lab.session.open_run", opened), patch(
            "lab.session.check_receiver_button", press
        ):
            session = ExperimentSession(Path("."), Path(path), "172.30.252", spring_receiver=True)
            try:
                session.request("start")
                self.wait_for(session, "running")
                for sequence in (1, 2):
                    session.request("button")
                    status = self.wait_for(session, "running")
                    self.assertEqual(status["events"][-1]["sequence"], sequence)
                    self.assertIsNone(status["led"])
                    self.assertIsNone(status["last_latency_ns"])
                self.assertEqual(
                    [event["stage"] for event in status["events"]],
                    ["INPUT", "SERVER_RECEIVED", "INPUT", "SERVER_RECEIVED"],
                )
                self.assertNotIn("timestamp_ns", status["events"][-1])
                session.request("stop")
                self.wait_for(session, "stopped")
            finally:
                session.close()

    def test_state_transitions_and_trace(self):
        initial = {"led": 1, "connected": [f"172.30.252.{i}" for i in range(11, 15)], "http": {}}

        @contextlib.contextmanager
        def opened(repo, output, prefix, profile, on_progress):
            output.mkdir()
            on_progress(4)
            yield object(), initial

        def press(lab, led, observe):
            self.assertEqual(led, 1)
            observe("INPUT", {"value": 0, "timestamp_ns": 1000})
            observe("APPLIED", {"value": 0, "timestamp_ns": 2100})
            return {"applied": {"value": 0}}

        with tempfile.TemporaryDirectory() as path, patch("lab.session.open_run", opened), patch("lab.session.check_button", press):
            session = ExperimentSession(Path("."), Path(path), "172.30.252")
            try:
                with self.assertRaises(SessionConflict):
                    session.request("button")
                session.request("start")
                self.wait_for(session, "running")
                with self.assertRaises(SessionConflict):
                    session.request("start")
                session.request("button")
                after = self.wait_for(session, "running")
                self.assertEqual(after["led"], 0)
                self.assertEqual(after["last_latency_ns"], 1100)
                session.request("stop")
                stopped = self.wait_for(session, "stopped")
                trace = (Path(path) / stopped["run_id"] / "events.jsonl").read_text().splitlines()
                self.assertEqual([json.loads(line)["stage"] for line in trace], ["INPUT", "APPLIED"])
            finally:
                session.close()

    def test_button_failure_remains_observable(self):
        @contextlib.contextmanager
        def opened(repo, output, prefix, profile, on_progress):
            output.mkdir()
            yield object(), {"led": 1, "connected": [], "http": {}}

        with tempfile.TemporaryDirectory() as path, patch("lab.session.open_run", opened), patch(
            "lab.session.check_button", side_effect=RuntimeError("output missing")
        ):
            session = ExperimentSession(Path("."), Path(path), "172.30.252")
            try:
                session.request("start")
                self.wait_for(session, "running")
                session.request("button")
                failed = self.wait_for(session, "failed")
                self.assertIn("output missing", failed["error"])
                checkpoint = Path(path) / failed["run_id"] / "session.json"
                self.assertEqual(json.loads(checkpoint.read_text())["phase"], "failed")
            finally:
                session.close()

    def test_cli_displays_control_options(self):
        root = Path(__file__).resolve().parents[1]
        output = subprocess.check_output([root / "pilab", "web", "--help"], text=True)
        self.assertIn("--control", output)

    def wait_for(self, session, state):
        end = monotonic() + 3
        while monotonic() < end:
            current = session.snapshot()
            if current["phase"] == state:
                return current
            sleep(0.02)
        self.fail(f"expected {state}, got {session.snapshot()}")


if __name__ == "__main__":
    unittest.main()
