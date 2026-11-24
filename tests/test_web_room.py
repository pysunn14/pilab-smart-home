from contextlib import contextmanager
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class WebRoomTest(unittest.TestCase):
    def test_cli_exposes_web_room_flag(self):
        result = subprocess.run([str(ROOT / "pilab"), "web", "--help"], check=True,
                                capture_output=True, text=True, timeout=5)
        self.assertRegex(result.stdout, r"(?m)^\s+--room(?:\s|$)")

    def test_stop_during_startup_does_not_advertise_running_or_allow_restart(self):
        from lab.session import SessionConflict
        from lab.web_room import WebRoom

        starting, release, cleanup, cleaned = Event(), Event(), Event(), Event()

        @contextmanager
        def fake_room(repo, output, prefix, on_progress):
            starting.set()
            if not release.wait(2):
                raise RuntimeError("test startup timed out")
            yield SimpleNamespace(rooms={"air": Mock()}, session=Mock(), serve_until=lambda stop: stop.wait(2))
            cleanup.set()
            if not cleaned.wait(2):
                raise RuntimeError("test cleanup timed out")

        with TemporaryDirectory() as directory, patch("lab.web_room.open_room", fake_room):
            room = WebRoom(ROOT, Path(directory), "172.30.250")
            try:
                room.request("start")
                self.assertTrue(starting.wait(1))
                self.assertEqual(room.snapshot()["phase"], "starting")
                self.assertIsNone(room.room_for("air"))
                with self.assertRaises(SessionConflict):
                    room.request("start")
                room.request("stop")
                release.set()
                self.assertTrue(cleanup.wait(1))
                self.assertEqual(room.snapshot()["phase"], "stopping")
                self.assertIsNone(room.room_for("air"))
                with self.assertRaises(SessionConflict):
                    room.request("start")
            finally:
                release.set()
                cleaned.set()
                room.close()
            self.assertEqual(room.snapshot()["phase"], "stopped")
