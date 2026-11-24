"""Drive the MainPi executable through its virtual GPIO edge descriptor."""

import json
import os
import struct
import sys
from pathlib import Path
from time import monotonic_ns
from urllib.request import urlopen


EDGES = Path("/board/button-edges")
MONITOR = "/app/mainpi/build/button-monitor"
DEBOUNCE_MS = 10


def run():
    os.mkfifo(EDGES, 0o600)
    # Keep the read descriptor alive between individual control commands.
    edge_fd = os.open(EDGES, os.O_RDWR | os.O_NONBLOCK)
    os.set_inheritable(edge_fd, True)
    os.environ["PILAB_TEST_EDGE_FD"] = str(edge_fd)
    os.execv(MONITOR, [MONITOR, "pipe:high", "20", str(DEBOUNCE_MS), "ws://spring:8080/socket"])


def edge(value):
    edge_fd = os.open(EDGES, os.O_WRONLY | os.O_NONBLOCK)
    try:
        timestamp = monotonic_ns()
        # Same single-write record as the inherited-pipe GPIO adapter.
        packet = struct.pack("=QB", timestamp, value)
        if os.write(edge_fd, packet) != len(packet):
            raise RuntimeError("incomplete GPIO edge write")
    finally:
        os.close(edge_fd)
    return timestamp


if __name__ == "__main__":
    if not sys.argv[1:]:
        run()
    elif sys.argv[1:] == ["probe"]:
        with urlopen("http://spring:8080/pilab/sessions", timeout=3) as response:
            print(json.dumps({"sessions": json.load(response)}))
    elif sys.argv[1:] == ["clock"]:
        from benchmark_workload import clock_probe

        clock_probe("http://spring:8080/pilab/monotonic")
    elif len(sys.argv) == 3 and sys.argv[1] == "edge" and sys.argv[2] in {"0", "1"}:
        value = int(sys.argv[2])
        print(json.dumps({"event": "input", "value": value, "timestamp_ns": edge(value)}))
    elif len(sys.argv) == 3 and sys.argv[1] == "benchmark-button":
        from benchmark_workload import fixed_pulse_replay

        fixed_pulse_replay(json.loads(sys.argv[2]), edge)
    else:
        raise SystemExit("unknown receiver node command")
