"""Verify manual and automatic control of the virtual AirPi servo."""

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
import re
import subprocess
from threading import Thread
from uuid import UUID, uuid4

from .control_web import call_environment, dashboard_handler
from .receiver_preview import AIR_AUTO_SERVO_PROFILE, AIR_SERVO_PROFILE
from .room_environment import RoomEnvironment
from .smoke import json_file, open_run
from .telemetry_smoke import spring_samples


@contextmanager
def virtual_air(lab, output, inputs):
    outputs = output / "virtual-servo"
    outputs.mkdir()
    name = "pilab-servo-" + uuid4().hex[:12]
    command = [
        *lab.command, "run", "--rm", "--no-deps", "-T", "--name", name,
        "--volume", f"{inputs}:/room:ro", "--volume", f"{outputs}:/servo",
        "air", "--room-file", "/room/air.txt", "--servo-file", "/servo/angle.txt",
    ]
    with (output / "airpi.log").open("w") as log, subprocess.Popen(
        command, env=lab.env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
    ) as air:
        try:
            yield outputs / "angle.txt"
        finally:
            # The explicitly named container belongs to this run alone.
            if air.poll() is None:
                subprocess.run(["docker", "stop", "--time", "2", name], env=lab.env,
                               capture_output=True, timeout=8)
                try:
                    air.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    air.kill()
                    raise


@contextmanager
def room_web(repo, inputs):
    handler = dashboard_handler(repo / "web", room_file=inputs / "air.txt")
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join()


def run(repo, output, prefix, angle):
    with open_run(repo, output, prefix, AIR_SERVO_PROFILE) as (lab, _):
        lab.progress(5)
        inputs = output / "virtual-room"
        inputs.mkdir()
        RoomEnvironment(inputs / "air.txt")
        with virtual_air(lab, output, inputs) as state:
            first = lab.wait(lambda: spring_samples(lab), bool, "AirPi 센서 첫 수신", timeout=12)[-1]
            before = int(state.read_text())
            if before != 90 or first["nodeId"] != "airpi":
                raise RuntimeError(f"AirPi 초기 상태 또는 센서 세션이 올바르지 않습니다. 각도 {before}, 센서 {first}")

            receipt = json.loads(lab.run(
                "exec", "-T", "spring", "curl", "-fsS", "--max-time", "6",
                "--data-urlencode", f"angle={angle}", "http://127.0.0.1:8080/pilab/air-servo",
            ))
            observed = int(state.read_text())
            command_id = receipt.get("commandId", "")
            if receipt != {"commandId": command_id, "angle": angle, "status": "applied"} or observed != angle:
                raise RuntimeError(f"Spring의 응답과 가상 출력이 일치하지 않습니다. 응답 {receipt}, 각도 {observed}")
            UUID(command_id)
            following = lab.wait(
                lambda: spring_samples(lab),
                lambda rows: bool(rows) and rows[-1]["seq"] > first["seq"],
                "서보 명령 뒤에도 센서 수신", timeout=7,
            )[-1]
            if following["runId"] != first["runId"]:
                raise RuntimeError("명령 전후 AirPi 실행 ID가 바뀌었습니다.")

            json_file(output / "summary.json", {
                "status": "passed", "initial_angle": before, "receipt": receipt, "observed_angle": observed,
                "sample_before": first, "sample_after": following,
            })
    lab.progress(5, "passed")
    print(f"AirPi 가상 서보 {angle}° 적용 확인 · {output / 'summary.json'}", flush=True)


def auto_receipts(lab):
    pattern = r"air_servo_auto_result commandId=([0-9a-f-]+) angle=(\d+) status=(\w+)"
    return [{"commandId": command_id, "angle": int(angle), "status": status}
            for command_id, angle, status in re.findall(pattern, lab.logs("spring"))]


def wait_dust(lab, dust, *, after=0, count=1):
    return lab.wait(
        lambda: [sample for sample in spring_samples(lab)
                 if sample["nodeId"] == "airpi" and sample["seq"] > after and sample["values"].get("dust") == dust],
        lambda rows: len(rows) >= count, f"먼지값 {dust} 수신 {count}건", timeout=12,
    )


def run_auto(repo, output, prefix):
    with open_run(repo, output, prefix, AIR_AUTO_SERVO_PROFILE) as (lab, _):
        lab.progress(5)
        inputs = output / "virtual-room"
        inputs.mkdir()
        with room_web(repo, inputs) as url:
            edits = [call_environment(url, {"dust": 80})]
            with virtual_air(lab, output, inputs) as state:
                before = wait_dust(lab, 80)[0]
                initial = int(state.read_text())
                if initial != 90:
                    raise RuntimeError(f"초기 가상 각도가 90°와 다릅니다. 관측 {initial}")

                edits.append(call_environment(url, {"dust": 120}))
                high = wait_dust(lab, 120, after=before["seq"], count=2)
                opened = lab.wait(lambda: int(state.read_text()), lambda angle: angle == 0, "가상 서보 0°")
                first = lab.wait(lambda: auto_receipts(lab), bool, "첫 자동 적용 응답", timeout=7)
                if len(first) != 1 or (first[0]["angle"], first[0]["status"]) != (0, "applied"):
                    raise RuntimeError(f"먼지값이 높은 동안 0° 적용 한 건을 기대했습니다. 관측 {first}")

                edits.append(call_environment(url, {"dust": 80}))
                after = wait_dust(lab, 80, after=high[-1]["seq"])
                restored = lab.wait(lambda: int(state.read_text()), lambda angle: angle == 90, "가상 서보 90° 복귀")
                lab.wait(lambda: auto_receipts(lab), lambda rows: len(rows) >= 2, "복귀 적용 응답", timeout=7)

        receipts = auto_receipts(lab)
        applications = [json.loads(line) for line in (output / "airpi.log").read_text().splitlines()
                        if line.startswith('{"event":"servo_applied",')]
        if ([(row["angle"], row["status"]) for row in receipts] != [(0, "applied"), (90, "applied")]
                or [(row["commandId"], row["angle"]) for row in receipts]
                != [(row["commandId"], row["angle"]) for row in applications]
                or len({row["commandId"] for row in receipts}) != 2):
            raise RuntimeError(f"자동 제어 응답과 가상 적용이 두 건으로 일치해야 합니다. 관측 {receipts}, {applications}")
        json_file(output / "summary.json", {
            "status": "passed", "room_edits": edits, "angles": [initial, opened, restored],
            "receipts": receipts, "applications": applications, "samples": [before, *high, *after],
        })
    lab.progress(5, "passed")
    print(f"AirPi 자동 서보 90° → 0° → 90° 확인 · {output / 'summary.json'}", flush=True)
