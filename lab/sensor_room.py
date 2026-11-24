"""Run a PiLab sensor workbench against isolated Spring and device containers."""

from contextlib import suppress
from http.server import ThreadingHTTPServer
import json
import signal
import subprocess
from threading import Thread
from uuid import uuid4

from .control_web import dashboard_handler
from .receiver_preview import FIRE_ROOM_PROFILE, LIGHT_ROOM_PROFILE
from .room_environment import RoomEnvironment, sensor_sample
from .smoke import json_file, open_run
from .telemetry_smoke import spring_samples, verify_history


PROFILES = {"fire": (FIRE_ROOM_PROFILE, "FirePi"), "light": (LIGHT_ROOM_PROFILE, "LightPi")}


def feed(input_pipe, snapshot, node):
    input_pipe.write(sensor_sample(snapshot, node) + "\n")
    input_pipe.flush()


def device_args(node, output):
    if node == "light":
        motor = output / "virtual-blind"
        motor.mkdir()
        return ["--volume", f"{motor}:/blind", "light", "--ambient-stdin",
                "--blind-output", "/blind/direction.txt"]
    alarm_dir = output / "virtual-alarm"
    alarm_dir.mkdir()
    return ["--volume", f"{alarm_dir}:/alarm", "fire", "--stdin", "--alarm-file", "/alarm/alarm.txt"]


def move_virtual_blind(lab, direction):
    try:
        raw = lab.run(
            "exec", "-T", "spring", "curl", "-sS", "--max-time", "6", "--write-out", "\n%{http_code}",
            "--data-urlencode", f"direction={direction}", "http://127.0.0.1:8080/pilab/light-blind", timeout=7,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Spring의 블라인드 명령 요청 시간이 초과됐습니다.") from error
    data, _, status = raw.rpartition("\n")
    if status == "409":
        return 409, {"error": "LightPi 연결이 없거나 앞선 블라인드 명령을 확인하고 있습니다."}
    if status != "200":
        raise RuntimeError(f"Spring 블라인드 명령 응답 HTTP {status}")
    receipt = json.loads(data)
    if (not isinstance(receipt, dict) or receipt.get("direction") != direction or not receipt.get("commandId")
            or receipt.get("status") not in ("applied", "unconfirmed")):
        raise ValueError("Spring의 블라인드 응답 형식이 올바르지 않습니다.")
    return 200, receipt


def run(repo, output, prefix, port, node, *, auto_blind=False):
    profile, label = PROFILES[node]
    device_room = RoomEnvironment(node=node)

    def move_blind(direction):
        return move_virtual_blind(lab, direction)

    handler = dashboard_handler(repo / "web", room_overrides={node: device_room},
                                move_blind=move_blind if node == "light" and not auto_blind else None,
                                auto_blind=auto_blind)

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with ThreadingHTTPServer(("127.0.0.1", port), handler) as server, open_run(
            repo, output, prefix, profile, auto_blind=auto_blind,
        ) as (lab, _):
            lab.progress(5)
            name = f"pilab-{node}-room-" + uuid4().hex[:12]
            log_path = output / f"{node}pi.log"
            command = [*lab.command, "run", "--rm", "--no-deps", "-T", "--name", name, *device_args(node, output)]
            with log_path.open("w") as log, subprocess.Popen(
                command, env=lab.env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                text=True, start_new_session=True,
            ) as device:
                try:
                    first = device_room.snapshot()
                    feed(device.stdin, first, node)
                    lab.wait(lambda: spring_samples(lab),
                             lambda rows: bool(rows) and rows[0]["nodeId"] == f"{node}pi"
                             and rows[0]["values"] == first["values"],
                             f"Spring의 {label} 첫 센서값 수신", timeout=12)
                    worker = Thread(target=server.serve_forever)
                    worker.start()
                    try:
                        print(f"PiLab {label}  http://127.0.0.1:{server.server_port}/#{node}-room", flush=True)
                        print("종료 Ctrl+C", flush=True)
                        revision = first["revision"]
                        while device.poll() is None:
                            snapshot = device_room.wait_for_change(revision, timeout=0.5)
                            if snapshot is not None:
                                feed(device.stdin, snapshot, node)
                                revision = snapshot["revision"]
                        raise RuntimeError(f"{label}가 종료됐습니다. 종료 코드 {device.returncode} · {log_path}")
                    finally:
                        server.shutdown()
                        worker.join()
                except KeyboardInterrupt:
                    pass
                finally:
                    with suppress(BrokenPipeError):
                        device.stdin.close()
                    if node == "light" and device.poll() is None:
                        subprocess.run(["docker", "stop", "--time", "2", name], env=lab.env,
                                       capture_output=True, timeout=8)
                    try:
                        device.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        subprocess.run(["docker", "stop", "--time", "2", name], env=lab.env,
                                       capture_output=True, timeout=8)
                        device.wait(timeout=6)
            receipts = spring_samples(lab)
            history = verify_history(lab, output, receipts, recreate=False)
            json_file(output / "summary.json", {
                "status": "stopped", "environment": device_room.snapshot(), "samples": history,
            })
            lab.progress(5, "stopped")
    finally:
        signal.signal(signal.SIGTERM, previous)
