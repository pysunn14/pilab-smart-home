import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, call, patch


from lab.connected_room import RoomSession
from lab.room_observation import LedOutput


ROOT = Path(__file__).resolve().parents[1]


class ConnectedRoomTest(unittest.TestCase):
    def test_room_cli_exposes_local_port_and_output(self):
        result = subprocess.run([str(ROOT / "pilab"), "lab", "room", "--help"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--port", result.stdout)
        self.assertIn("--output", result.stdout)

    def test_led_reader_waits_for_a_complete_gpio_record(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "lightpi.log"
            ready = {"event": "ready", "on": False}
            output = {"event": "gpio", "offset": 18, "on": True, "commandId": "mainpi/run/1"}
            noise = {"event": "blind_output_applied", "direction": "UP"}
            path.write_text("starting\n" + json.dumps(ready) + "\n" + json.dumps(noise) + "\n")
            led = LedOutput(path)
            self.assertEqual(led.read(), (False, None))
            with path.open("a") as log:
                log.write(json.dumps(output))
            self.assertEqual(led.read(), (False, None))
            with path.open("a") as log:
                log.write("\n")
            self.assertEqual(led.read(), (True, output))

    @patch("lab.connected_room.node_command")
    @patch("lab.connected_room.main_events", return_value=[])
    def test_button_releases_gpio_when_press_response_is_lost(self, _events, node_command):
        with TemporaryDirectory() as directory:
            lab = Mock(output=Path(directory))
            node_command.side_effect = [RuntimeError("Lost GPIO write response"), "released"]
            session = RoomSession(lab, Mock())
            session.press()
            self.assertEqual(node_command.call_args_list, [call(lab, "edge", "0"), call(lab, "edge", "1")])
            self.assertIn("Lost GPIO write response", session.snapshot()["buttonError"])
