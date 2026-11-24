"""Run the four connected Pi processes behind one interactive PiLab workbench."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import signal
import subprocess
from threading import Lock, Thread
from time import monotonic
from uuid import uuid4

from .control_web import dashboard_handler
from .receiver_preview import ROOM_PROFILE, main_events, node_command, parse_button_receipts
from .room_environment import RoomEnvironment
from .room_observation import LedOutput, observe_room
from .sensor_room import device_args, feed
from .session import SessionConflict
from .smoke import json_file, open_run
from .telemetry_smoke import spring_samples, verify_history


SENSORS = ("air", "fire", "light")


class RoomSession:
    def __init__(self, lab, led):
        self.lab = lab
        self.led = led
        self.lock = Lock()
        self.worker = None
        self.closed = False
        self.state = {"spring": False, "connected": [], "readings": {}, "outputs": {}}
        self.observed_at = None
        self.events = []
        self.button_error = None
        self.observation_error = None
        self.busy = False

    def snapshot(self):
        with self.lock:
            return {**self.state, "mode": "room",
                    "phase": "stopping" if self.closed else "applying" if self.busy else "running",
                    "observedAt": self.observed_at, "events": list(self.events),
                    "buttonError": self.button_error, "observationError": self.observation_error}

    def observe(self):
        try:
            state = observe_room(self.lab, self.led)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            with self.lock:
                if self.observation_error is None:
                    print(f"PiLab 상태 조회 실패 · {error}", flush=True)
                self.observation_error = "Spring 또는 장치 출력을 조회하지 못했습니다."
                self.state = {"spring": False, "connected": [], "readings": {}, "outputs": {}}
            return
        with self.lock:
            self.state = state
            self.observed_at = datetime.now(timezone.utc).isoformat()
            self.observation_error = None

    def request(self, action):
        with self.lock:
            if action != "button":
                raise SessionConflict("통합 작업대는 터미널에서 시작하고 종료합니다.")
            if self.closed or self.busy:
                raise SessionConflict("이전 조작이 처리 중입니다.")
            if not {"main", "light"}.issubset(self.state["connected"]):
                raise SessionConflict("MainPi와 LightPi가 Spring에 연결되어야 합니다.")
            self.events = []
            self.button_error = None
            self.busy = True
            self.worker = Thread(target=self.press, name="pilab-room-button")
            self.worker.start()

    def record(self, stage, evidence):
        with self.lock:
            self.events.append({"stage": stage, **evidence})

    def press(self):
        try:
            released = sum(row["event"] == "button_released" for row in main_events(self.lab))
            try:
                edge = json.loads(node_command(self.lab, "edge", "0"))
                self.record("input", edge)

                def receive():
                    rows = parse_button_receipts(self.lab.logs("spring"))
                    return next((row for row in rows if row["timestamp_ns"] >= edge["timestamp_ns"]), None)

                receipt = self.lab.wait(receive, bool, "Spring 버튼 수신", timeout=5)
                self.record("received", receipt)
            finally:
                node_command(self.lab, "edge", "1")

            self.lab.wait(lambda: sum(row["event"] == "button_released" for row in main_events(self.lab)),
                          lambda count: count > released, "MainPi 버튼 떼기", timeout=4)
            command = receipt["commandId"]

            def applied():
                _, output = self.led.read()
                return output if output and output["commandId"] == command else None

            output = self.lab.wait(applied, bool, "LightPi 가상 GPIO 적용", timeout=6)
            self.record("applied", output)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            print(f"PiLab 버튼 확인 실패 · {error}", flush=True)
            with self.lock:
                self.button_error = str(error)
        finally:
            with self.lock:
                record = {"error": self.button_error, "events": self.events}
            try:
                with (self.lab.output / "button-events.jsonl").open("a") as log:
                    log.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as error:
                print(f"PiLab 버튼 기록 저장 실패 · {error}", flush=True)
                with self.lock:
                    self.button_error = "버튼 경로 기록을 저장하지 못했습니다."
            finally:
                with self.lock:
                    self.busy = False

    def close(self):
        with self.lock:
            self.closed = True
            worker = self.worker
        if worker is not None:
            worker.join()


@contextmanager
def running_device(lab, role, args):
    name = f"pilab-room-{role}-" + uuid4().hex[:12]
    command = [*lab.command, "run", "--rm", "--no-deps", "-T", "--name", name, *args]
    with (lab.output / f"{role}pi.log").open("w") as log, subprocess.Popen(
        command, env=lab.env, stdout=log, stderr=subprocess.STDOUT, text=True, start_new_session=True,
        stdin=subprocess.DEVNULL if role == "air" else subprocess.PIPE,
    ) as process:
        try:
            yield process
        finally:
            try:
                if process.poll() is None:
                    subprocess.run(["docker", "stop", "--time", "2", name], env=lab.env,
                                   capture_output=True, timeout=8)
                    process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                raise
            finally:
                if process.stdin is not None:
                    process.stdin.close()


def require_running(devices):
    for role, process in devices.items():
        if process.poll() is not None:
            raise RuntimeError(f"{role} 장치가 종료됐습니다. {role}pi.log를 확인하세요.")


def ready(lab, devices):
    require_running(devices)
    return {row["nodeId"] for row in spring_samples(lab)}


def service_loop(session, devices, rooms, revisions, stop):
    next_observation = 0.0
    while not stop.wait(0.1):
        require_running(devices)
        for role in ("fire", "light"):
            sample = rooms[role].snapshot()
            if sample["revision"] != revisions[role]:
                feed(devices[role].stdin, sample, role)
                revisions[role] = sample["revision"]
        if monotonic() >= next_observation:
            session.observe()
            next_observation = monotonic() + 1.0


@dataclass
class RunningRoom:
    session: RoomSession
    rooms: dict
    devices: dict
    revisions: dict

    def serve_until(self, stop):
        service_loop(self.session, self.devices, self.rooms, self.revisions, stop)


@contextmanager
def open_room(repo, output, prefix, on_progress=None):
    with open_run(repo, output, prefix, ROOM_PROFILE, on_progress, auto_blind=True) as (lab, _):
        lab.progress(5)
        inputs = output / "virtual-room"
        inputs.mkdir()
        servo = output / "virtual-servo"
        servo.mkdir()
        rooms = {role: RoomEnvironment(inputs / "air.txt" if role == "air" else None, node=role)
                 for role in SENSORS}
        air_args = ["--volume", f"{inputs}:/room:ro", "--volume", f"{servo}:/servo",
                    "air", "--room-file", "/room/air.txt", "--servo-file", "/servo/angle.txt"]
        session = RoomSession(lab, LedOutput(output / "lightpi.log"))
        with ExitStack() as stack:
            devices = {role: stack.enter_context(running_device(
                lab, role, air_args if role == "air" else device_args(role, output))) for role in SENSORS}
            revisions = {}
            for role in ("fire", "light"):
                sample = rooms[role].snapshot()
                feed(devices[role].stdin, sample, role)
                revisions[role] = sample["revision"]
            lab.wait(lambda: ready(lab, devices), {role + "pi" for role in SENSORS}.issubset,
                     "세 센서 Pi의 첫 샘플 수신", timeout=16)
            session.observe()
            if set(session.snapshot()["connected"]) != {"main", *SENSORS}:
                raise RuntimeError("Spring에 연결된 Pi 네 대를 확인하지 못했습니다.")
            try:
                yield RunningRoom(session, rooms, devices, revisions)
            finally:
                session.close()
        history = verify_history(lab, output, recreate=False)
        json_file(output / "summary.json", {"status": "stopped", "lastObservation": session.snapshot(),
                                           "samples": history})
        lab.progress(5, "stopped")


def run(repo, output, prefix, port):
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with ThreadingHTTPServer(("127.0.0.1", port), None) as server, open_room(repo, output, prefix) as room:
            server.RequestHandlerClass = dashboard_handler(
                repo / "web", room.session, room_overrides=room.rooms, auto_blind=True,
            )
            worker = Thread(target=server.serve_forever, name="pilab-room-web")
            worker.start()
            try:
                print(f"PiLab 통합 작업대  http://127.0.0.1:{server.server_port}/room.html", flush=True)
                print("종료 Ctrl+C", flush=True)
                room.serve_until(room.session.lab.cancel)
            except KeyboardInterrupt:
                pass
            finally:
                server.shutdown()
                worker.join()
    finally:
        signal.signal(signal.SIGTERM, previous)
