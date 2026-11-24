"""Read the sources of truth for the connected-room dashboard."""

import json
from pathlib import Path
import threading

from .smoke import role_ips


OUTPUT_FILES = {
    "blind": ("virtual-blind/direction.txt", {-1, 0, 1}),
    "alarm": ("virtual-alarm/alarm.txt", {0, 1}),
    "servo": ("virtual-servo/angle.txt", range(181)),
}


class LedOutput:
    def __init__(self, path):
        self.path = path
        self.position = 0
        self.value = None
        self.command = None
        self.lock = threading.Lock()

    def read(self):
        with self.lock, self.path.open() as log:
            log.seek(self.position)
            while line := log.readline():
                if not line.endswith("\n"):
                    break
                self.position = log.tell()
                if not line.startswith('{"event":'):
                    continue
                event = json.loads(line)
                if event["event"] == "ready":
                    self.value = event["on"]
                    self.command = None
                elif event["event"] == "gpio" and event["offset"] == 18:
                    self.value = event["on"]
                    self.command = event
            return self.value, self.command


def actuator_value(path: Path, valid):
    try:
        value = int(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return value if value in valid else None


def observe_room(lab, led):
    raw = lab.run("exec", "-T", "spring", "curl", "-fsS", "--max-time", "2",
                  "http://127.0.0.1:8080/pilab/room-state", timeout=4)
    spring = json.loads(raw)
    connected = [role for role, address in role_ips(lab.env["PILAB_PREFIX"]).items()
                 if address in spring["sessions"]]
    outputs = {name: actuator_value(lab.output / file, valid) for name, (file, valid) in OUTPUT_FILES.items()}
    outputs["led"] = led.read()[0]
    return {"spring": True, "connected": connected, "readings": spring["readings"], "outputs": outputs}
