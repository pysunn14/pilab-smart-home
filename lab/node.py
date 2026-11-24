"""Run one legacy Pi's Python bridge and C program in the same namespace."""

import json
import os
import signal
import ssl
import subprocess
import sys
from pathlib import Path
from time import monotonic, monotonic_ns, sleep
from urllib.request import urlopen


BOARD = Path("/board")
TCP = {"main": 8000, "fire": 5000, "air": 8080, "light": 8888}
FIXTURE = {"main": {}, "fire": {0: 64, 2: 576}, "air": {0: 67}, "light": {0: 350}}


def initialize(role):
    gpio = BOARD / "gpio"
    gpio.mkdir(parents=True)
    (gpio / "export").touch()
    for pin in (13, 18, 19, 20, 26):
        directory = gpio / f"gpio{pin}"
        directory.mkdir()
        (directory / "direction").touch()
        (directory / "value").write_text("1\n" if pin == 20 else "0\n")
    (BOARD / "spi").touch()
    for channel, sample in FIXTURE[role].items():
        (BOARD / f"adc{channel}").write_text(str(sample))


def is_listening(port):
    return any(
        line.split()[1].endswith(f":{port:04X}") and line.split()[3] == "0A"
        for line in Path("/proc/net/tcp").read_text().splitlines()[1:]
    )


def start(role):
    initialize(role)
    bridge = subprocess.Popen([sys.executable, "-u", f"/app/{role}/bridge.py"])
    processes = [bridge]
    try:
        deadline = monotonic() + 30
        while monotonic() < deadline and bridge.poll() is None:
            ready = is_listening(TCP[role])
            if role == "light":
                ready = ready and (BOARD / "wss-ready").exists()
            if ready:
                break
            sleep(0.1)
        else:
            raise RuntimeError(f"{role}: 로컬 TCP 브리지가 준비되지 않았습니다.")
        native = subprocess.Popen([f"/app/{role}/node"])
        processes.append(native)
        while all(process.poll() is None for process in processes):
            sleep(0.25)
        raise RuntimeError(f"{role}: 원본 프로세스가 종료되었습니다.")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def probe(include_identity=False):
    context = ssl._create_unverified_context()  # The Compose runtime has a per-container lab certificate.
    server = os.environ["PILAB_SERVER_IP"]
    result = {}
    for name in ("data", "control"):
        with urlopen(f"https://{server}:8080/home/{name}", context=context, timeout=3) as response:
            result[name] = json.load(response)
    resource = "session-identities" if include_identity else "sessions"
    with urlopen(f"https://{server}:8080/pilab/{resource}", context=context, timeout=3) as response:
        sessions = json.load(response)
    result["sessions"] = list(sessions) if include_identity else sessions
    if include_identity:
        result["session_ids"] = sessions
    print(json.dumps(result))


def write_input(target, value, *, before_write=False):
    pending = target.with_suffix(".pending")
    pending.write_text(f"{value}\n")
    started = monotonic_ns() if before_write else None
    pending.replace(target)
    return started if before_write else monotonic_ns()


def set_button(value, *, before_write=False):
    return write_input(BOARD / "gpio/gpio20/value", value, before_write=before_write)


def p0_http():
    from benchmark_workload import http_load
    from threading import Event, Thread

    done = Event()
    reader = Thread(target=lambda: (sys.stdin.readline(), done.set()), daemon=True)
    reader.start()
    origin = monotonic_ns()
    http_load(os.environ["PILAB_SERVER_IP"], origin, None, 500_000_000, done)


def p0_adc(role, value):
    if role not in {"fire", "air", "light"} or not 0 <= value <= 1023:
        raise SystemExit("unsupported ADC fixture")
    stamp = write_input(BOARD / "adc0", value)
    print(json.dumps({"timestamp_ns": stamp, "value": value}))


def p0_burst(pairs):
    from benchmark_workload import emit, pause_until

    origin = monotonic_ns() + 100_000_000
    for pair in range(pairs):
        for edge in range(2):
            planned = origin + (pair * 5_000 + edge * 360) * 1_000_000
            pause_until(planned)
            actual = set_button("0")
            emit({"event": "input", "sample": 2 * pair + edge + 1, "timestamp_ns": actual, "scheduled_ns": planned})
            pause_until(actual + 160_000_000)
            emit({"event": "release", "timestamp_ns": set_button("1")})
    pause_until(origin + pairs * 5_000_000_000)
    emit({"event": "run_end", "timestamp_ns": monotonic_ns()})


def button(value):
    print(json.dumps({"event": "input", "value": int(value), "timestamp_ns": set_button(value)}))


def stop(signum, _):
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    if sys.argv[1:] == ["probe"]:
        probe()
    elif sys.argv[1:] == ["p0-probe"]:
        probe(include_identity=True)
    elif len(sys.argv) == 3 and sys.argv[1] == "button" and sys.argv[2] in {"0", "1"}:
        button(sys.argv[2])
    elif sys.argv[1:] == ["p0-http"]:
        p0_http()
    elif sys.argv[1:] == ["clock"]:
        from benchmark_workload import clock_probe

        server = os.environ["PILAB_SERVER_IP"]
        clock_probe(f"https://{server}:8080/pilab/monotonic", ssl._create_unverified_context())
    elif sys.argv[1:] == ["p0-clock"]:
        print(json.dumps({"timestamp_ns": monotonic_ns(),
                          "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                          "monotonic_offset": next(line.split()[1:] for line in
                                                   Path("/proc/self/timens_offsets").read_text().splitlines()
                                                   if line.split()[0] == "monotonic")}))
    elif len(sys.argv) == 3 and sys.argv[1] == "p0-adc":
        p0_adc(os.environ["PILAB_ROLE"], int(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "p0-burst" and os.environ["PILAB_ROLE"] == "main":
        p0_burst(int(sys.argv[2]))
    elif sys.argv[1:] == ["p0-cut-tcp"] and os.environ["PILAB_ROLE"] == "main":
        (BOARD / "cut-local-tcp").touch(exist_ok=False)
    elif len(sys.argv) == 3 and sys.argv[1] == "benchmark-button" and os.environ.get("PILAB_ROLE") == "main":
        from benchmark_workload import replay

        replay(json.loads(sys.argv[2]), set_button, os.environ["PILAB_SERVER_IP"])
    elif len(sys.argv) == 3 and sys.argv[1] == "receiver-benchmark-button" and os.environ.get("PILAB_ROLE") == "main":
        from benchmark_workload import fixed_pulse_replay

        fixed_pulse_replay(json.loads(sys.argv[2]), lambda value: set_button(value, before_write=True))
    elif not sys.argv[1:]:
        signal.signal(signal.SIGTERM, stop)
        start(os.environ["PILAB_ROLE"])
    else:
        raise SystemExit("unknown lab node command")
