"""Start an isolated baseline and verify an input reached the device output."""

import hashlib
import ipaddress
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from . import receiver_preview
from .legacy import HERE, REVISIONS, git, stage


DEFAULT_PREFIX = "172.30.251"
ROLE_HOSTS = {"main": 11, "fire": 12, "air": 13, "light": 14}
EXPECTED_DATA = {"infra": 951, "gas": 40, "dust": 11, "humidity": 42, "light": 350}
STEPS = ("실험 소스 준비", "이미지 빌드", "컨테이너 시작", "장치 준비 확인", "입출력 확인")


def json_file(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def json_lines(output):
    return [json.loads(line) for line in output.splitlines() if line.startswith('{"event":')]


def led_transitions(records, initial):
    changes = []
    for record in records:
        if record.get("event") == "gpio" and record.get("pin") == 18 and record["value"] != initial:
            initial = record["value"]
            changes.append(initial)
    return changes


class CleanupError(RuntimeError):
    pass


class Lab:
    def __init__(self, project, build, output, prefix, on_progress=None, compose_name="compose.yaml", *, overlays=()):
        self.command = [
            "docker", "compose", "--ansi", "never", "--progress", "plain",
            "-p", project, "-f", str(HERE / compose_name),
        ]
        for overlay in overlays:
            self.command.extend(["-f", str(HERE / overlay)])
        self.env = dict(os.environ, PILAB_BUILD=str(build), PILAB_PREFIX=prefix)
        self.output = output
        self.cancel = Event()
        self.part = 0
        self.on_progress = on_progress

    def run(self, *args, check=True, timeout=12):
        result = subprocess.run(
            [*self.command, *args], env=self.env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, check=False, timeout=timeout,
        )
        if check and result.returncode:
            raise RuntimeError(result.stdout.strip() or f"Docker Compose {args[0]} 실패")
        return result.stdout

    def log_command(self, name, *args, check=True):
        with (self.output / name).open("w") as log:
            result = subprocess.run(
                [*self.command, *args], env=self.env, stdout=log, stderr=subprocess.STDOUT,
            )
        if check and result.returncode:
            raise RuntimeError(f"Docker Compose {args[0]} 실패. 로그: {self.output / name}")

    def progress(self, number, status="running"):
        self.part = number
        bar = "#" * number + "-" * (len(STEPS) - number)
        print(f"[{bar}] {number}/{len(STEPS)} {STEPS[number - 1]} ({status})", flush=True)
        json_file(self.output / "checkpoint.json", {"step": number, "status": status})
        if self.on_progress is not None:
            self.on_progress(number)

    def heartbeat(self):
        while not self.cancel.wait(15):
            print(f"PiLab 실행 중 · {self.part}/{len(STEPS)} · {self.output}", flush=True)

    def wait(self, read, accept, description, timeout=45):
        deadline = monotonic() + timeout
        latest = None
        while monotonic() < deadline:
            latest = read()
            if accept(latest):
                return latest
            self.cancel.wait(0.4)
        raise RuntimeError(f"{description} 확인 시간 초과. 최근 관측: {latest}")

    def check_processes(self):
        output = self.run("ps", "--all", "--format", "json").strip()
        containers = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
        for container in containers:
            if container["State"] in {"dead", "exited"}:
                raise RuntimeError(f"{container['Service']} 컨테이너 종료. 로그: {self.output / 'services.log'}")

    def logs(self, role):
        return self.run("logs", "--no-log-prefix", role)

    def probe(self):
        output = self.run("exec", "-T", "main", "python", "/app/node.py", "probe", check=False)
        for line in output.splitlines():
            if line.startswith('{"data"'):
                return json.loads(line)
        return {"probe_error": output.strip()}


def check_integrated(lab, expected_ips):
    def observe():
        lab.check_processes()
        inputs = lab.probe()
        light_trace = json_lines(lab.logs("light"))
        states = led_transitions(light_trace, initial=None)
        return {
            "http": inputs,
            "connected": sorted(inputs.get("sessions", [])),
            "led": states[-1] if states else None,
        }

    def ready(observed):
        http = observed["http"]
        data = http.get("data", {})
        control = http.get("control", {})
        return (
            set(observed["connected"]) == expected_ips
            and all(data.get(key) == value for key, value in EXPECTED_DATA.items())
            and control.get("buttonSignal") is True
            and observed["led"] is not None
            and bool(observed["led"]) == control.get("lightControl")
        )

    return lab.wait(observe, ready, "네 Pi의 WebSocket·센서 반영·LED 초기 상태")


def check_button(lab, initial, observe=None):
    start_index = len(json_lines(lab.logs("light")))
    input_event = json_lines(lab.run("exec", "-T", "main", "python", "/app/node.py", "button", "0"))[0]
    expected = 1 - initial
    completed = False
    try:
        if observe is not None:
            observe("INPUT", input_event)

        def read_output():
            lab.check_processes()
            return json_lines(lab.logs("light"))[start_index:]

        output = lab.wait(read_output, lambda events: bool(led_transitions(events, initial)), "LED 출력 변화")
        transitions = led_transitions(output, initial)
        if transitions != [expected]:
            raise RuntimeError(f"LED 상태 전이가 예상과 다릅니다. 관측: {transitions}, 기대: {[expected]}")
        applied = next(e for e in output if e.get("event") == "gpio" and e.get("pin") == 18 and e["value"] == expected)
        if applied["timestamp_ns"] < input_event["timestamp_ns"]:
            raise RuntimeError("LED 출력 기록이 입력 기록보다 앞섭니다.")
        if observe is not None:
            observe("APPLIED", applied)
        observed = lab.wait(
            lab.probe, lambda result: result.get("control", {}).get("lightControl") is bool(expected),
            "Spring 조명 상태",
        )
        if led_transitions(read_output(), initial) != [expected]:
            raise RuntimeError("같은 입력에서 LED 출력이 다시 바뀌었습니다.")
        completed = True
        return {"input": input_event, "applied": applied, "control": observed["control"]}
    finally:
        before_release = len(json_lines(lab.logs("main"))) if completed else 0
        lab.run("exec", "-T", "main", "python", "/app/node.py", "button", "1")
        if completed:
            lab.wait(
                lambda: json_lines(lab.logs("main"))[before_release:],
                lambda events: any(e.get("event") == "gpio_read" and e.get("value") == 1 for e in events),
                "MainPi의 버튼 떼기 감지",
            )


def role_ips(prefix):
    return {role: f"{prefix}.{host}" for role, host in ROLE_HOSTS.items()}


@contextmanager
def open_run(repo, output, prefix, profile="virtual-io-smoke", on_progress=None, *,
             ack_timeout_ms=0, drop_acks=0, drop_first_light_command=False, persist_light_queue=False,
             auto_blind=False):
    integrated = profile == receiver_preview.INTEGRATED_PROFILE
    connected_room = profile == receiver_preview.ROOM_PROFILE
    light_room = connected_room or profile == receiver_preview.LIGHT_ROOM_PROFILE
    light_lab = integrated or light_room or profile in (
        receiver_preview.LIGHT_ACK_DELAY_PROFILE, receiver_preview.LIGHT_ACK_RETRY_PROFILE,
        receiver_preview.LIGHT_RECONNECT_PROFILE, receiver_preview.LIGHT_BENCHMARK_PROFILE,
    )
    hold_light_ack = light_lab and not light_room and profile != receiver_preview.LIGHT_BENCHMARK_PROFILE and not drop_acks
    telemetry_role = receiver_preview.TELEMETRY_ROLES.get(profile)
    sensor_roles = ("air", "fire") if integrated or connected_room else (telemetry_role,) if telemetry_role else ()
    persist_sensor_history = bool(sensor_roles) or light_room
    receiver = profile == receiver_preview.PROFILE or light_lab or telemetry_role is not None
    compose_name = "compose.receiver.yaml" if receiver else "compose.yaml"
    overlays = ["compose.light-ack-delay.yaml"] if light_lab else []
    if light_room:
        overlays.append("compose.light-room.yaml")
    overlays.extend(f"compose.{role}.yaml" for role in sensor_roles)
    revisions = REVISIONS
    if receiver:
        revision = git(repo, "rev-parse", "HEAD").decode().strip()
        devices = ("main", "light", *sensor_roles) if light_lab else sensor_roles or ("main",)
        revisions = dict.fromkeys(("spring", *devices), revision)
    network = ipaddress.IPv4Network(prefix + ".0/24")
    project = "pilab-" + uuid4().hex[:12]
    output.mkdir(parents=True)
    manifest = {
        "project": project, "git_revisions": revisions, "network": str(network),
        "profile": profile, "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if light_room:
        manifest["blind_mode"] = "automatic" if auto_blind else "manual"
    if light_lab:
        manifest["light_queue_persistent"] = persist_light_queue
        manifest["light_ack"] = {"timeout_ms": ack_timeout_ms, "drop_count": drop_acks,
                                 "hold_first": hold_light_ack}
        manifest["light_command"] = {"drop_first_before_send": drop_first_light_command}
    json_file(output / "manifest.json", manifest)

    with tempfile.TemporaryDirectory(prefix="pilab-build-") as working:
        build = Path(working)
        lab = Lab(project, build, output, prefix, on_progress, compose_name, overlays=overlays)
        if persist_sensor_history:
            lab.env["PILAB_SENSOR_DB_PATH"] = "/var/lib/pilab/sensor-samples.sqlite"
        if light_room:
            lab.env["PILAB_LIGHT_AUTO_BLIND"] = str(auto_blind).lower()
        if "air" in sensor_roles:
            lab.env["PILAB_AIR_AUTO_SERVO"] = str(connected_room or profile == receiver_preview.AIR_AUTO_SERVO_PROFILE).lower()
        if "fire" in sensor_roles:
            lab.env["PILAB_FIRE_AUTO_ALARM"] = str(connected_room or profile == receiver_preview.FIRE_ROOM_PROFILE).lower()
        if light_lab:
            lab.env.update(PILAB_LIGHT_STATE_FILE="/var/lib/pilab/light-queue.json" if persist_light_queue else "",
                           PILAB_HOLD_LIGHT_ACK=str(int(hold_light_ack)),
                           PILAB_ACK_TIMEOUT_MS=str(ack_timeout_ms), PILAB_DROP_LIGHT_ACKS=str(drop_acks),
                           PILAB_DROP_FIRST_LIGHT_COMMAND=str(int(drop_first_light_command)))
        thread = Thread(target=lab.heartbeat, daemon=True)
        thread.start()
        resources_owned = False
        try:
            lab.progress(1)
            if telemetry_role is not None:
                receiver_preview.stage_telemetry(repo, build, telemetry_role)
            elif receiver:
                receiver_preview.stage(repo, build, drop_first_light_command=drop_first_light_command)
                if light_lab:
                    receiver_preview.stage_light_ack_delay(repo, build)
                for role in sensor_roles:
                    receiver_preview.stage_telemetry_node(repo, build, role)
            elif profile == receiver_preview.LEGACY_PROFILE:
                receiver_preview.stage_legacy(repo, build)
            else:
                stage(repo, build)
            manifest["build_inputs"] = {
                str(path.relative_to(build)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(build.rglob("*")) if path.is_file()
            }
            manifest["build_inputs"][compose_name] = hashlib.sha256((HERE / compose_name).read_bytes()).hexdigest()
            for overlay in overlays:
                manifest["build_inputs"][overlay] = hashlib.sha256((HERE / overlay).read_bytes()).hexdigest()
            json_file(output / "manifest.json", manifest)
            lab.log_command("docker-version.log", "version")
            lab.progress(2)
            resources_owned = True
            build_services = ("spring", telemetry_role) if telemetry_role is not None else ()
            lab.log_command("build.log", "build", *build_services)
            lab.progress(3)
            if integrated:
                ready_services = ("--wait", "spring", "main", "light")
            elif light_room:
                ready_services = ("--wait", "spring", "main")
            else:
                ready_services = ("--wait", "spring") if telemetry_role is not None else ()
            lab.log_command("startup.log", "up", "-d", "--no-build", *ready_services)
            lab.progress(4)
            if telemetry_role is not None:
                ready = None
            elif receiver:
                ready = receiver_preview.check_ready(lab, role_ips(prefix)["main"])
            else:
                ready = check_integrated(lab, set(role_ips(prefix).values()))
            yield lab, ready
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            json_file(output / "checkpoint.json", {"step": lab.part, "status": "failed", "error": str(error)})
            raise
        except KeyboardInterrupt:
            json_file(output / "checkpoint.json", {"step": lab.part, "status": "interrupted"})
            raise
        finally:
            lab.cancel.set()
            thread.join()
            if resources_owned:
                try:
                    lab.log_command("services.log", "logs", "--no-color", check=False)
                finally:
                    try:
                        cleanup_args = ("--volumes",) if persist_sensor_history else ()
                        lab.log_command("cleanup.log", "down", "--rmi", "all", *cleanup_args)
                    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                        raise CleanupError(str(error)) from error


def run(repo, output, prefix):
    with open_run(repo, output, prefix) as (lab, ready):
        lab.progress(5)
        evidence = check_button(lab, ready["led"])
        if set(lab.probe().get("sessions", [])) != set(role_ips(prefix).values()):
            raise RuntimeError("LED 적용 확인 시점에 네 Pi의 WebSocket 연결이 유지되지 않았습니다.")

    result = {"ready": ready, "button": evidence}
    json_file(output / "summary.json", result)
    lab.progress(len(STEPS), "passed")
    print(f"네 Pi 연결과 버튼 → 가상 LED 적용 확인. 결과: {output / 'summary.json'}")
    return result
