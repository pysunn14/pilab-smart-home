"""Exercise the current four Pi processes through one Spring instance."""

import re
import subprocess
from contextlib import ExitStack, contextmanager

from .light_ack_delay import COMMAND, RELEASE, gpio_states, observed, press, verify_held
from .receiver_preview import INTEGRATED_PROFILE
from .smoke import json_file, json_lines, open_run, role_ips
from .telemetry_smoke import SAMPLES, spring_json, spring_samples, verify_history


def validate_sensor_samples(receipts, rounds):
    if len(receipts) != len(SAMPLES) * rounds:
        raise RuntimeError(f"센서 수신 건수가 예상과 다릅니다. 관측: {receipts}")
    for role, samples in SAMPLES.items():
        records = [row for row in receipts if row["nodeId"] == f"{role}pi"]
        sequences = [(row["seq"], row["values"]) for row in records]
        run_ids = {row["runId"] for row in records}
        if (sequences != list(enumerate(samples[:rounds], 1)) or len(run_ids) != 1
                or not re.fullmatch(r"[0-9a-f]{32}", next(iter(run_ids)))
                or any(not row.get("receivedAt") for row in records)):
            raise RuntimeError(f"{role} 센서 샘플의 순번, 실행 ID 또는 값이 예상과 다릅니다. 관측: {records}")


def validate_light(command, events, gpio, pending):
    expected = [("sent", command, "true", ""), ("applied", command, "true", "CONFIRMED")]
    writes = [(row["offset"], row["on"], row["commandId"]) for row in gpio]
    if events != expected or writes != [(18, True, command)] or pending:
        raise RuntimeError(f"조명 한 번 적용 또는 큐 완료 확인 실패. Spring {events}, GPIO {gpio}, 큐 {pending}")


@contextmanager
def sensor_stream(lab, output, role):
    command = [*lab.command, "run", "--rm", "--no-deps", "-T", role, "--stdin"]
    with (output / f"{role}-send.log").open("w") as log, subprocess.Popen(
            command, env=lab.env, text=True, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
    ) as sender:
        try:
            yield sender
            sender.stdin.close()
            if sender.wait(timeout=10) != 0:
                raise RuntimeError(f"{role} 센서 송신기 종료 오류")
        finally:
            if sender.poll() is None:
                sender.kill()


def send_round(lab, streams, round_number):
    for role, sender in streams.items():
        if sender.poll() is not None:
            raise RuntimeError(f"{role} 센서 송신기가 입력 전에 종료됐습니다.")
        values = SAMPLES[role][round_number - 1]
        sender.stdin.write(" ".join(str(value) for value in values.values()) + "\n")
        sender.stdin.flush()
    receipts = observed(lab, lambda: spring_samples(lab),
                        lambda rows: len(rows) >= round_number * len(streams), "Spring의 센서 수신")
    validate_sensor_samples(receipts, round_number)
    return receipts


def run(repo, output, prefix):
    addresses = role_ips(prefix)
    with open_run(repo, output, prefix, INTEGRATED_PROFILE) as (lab, _):
        lab.progress(5)
        before = spring_json(lab, "/home/data")

        def light_events():
            return json_lines(lab.logs("light"))

        def pending():
            return spring_json(lab, "/pilab/light-pending")

        observed(lab, lambda: lab.logs("spring"),
                 lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                 "LightPi 초기 꺼짐 상태")
        with ExitStack() as stack:
            streams = {role: stack.enter_context(sensor_stream(lab, output, role)) for role in SAMPLES}
            send_round(lab, streams, 1)
            sessions = spring_json(lab, "/pilab/session-identities")
            if set(sessions) != set(addresses.values()) or len(set(sessions.values())) != len(addresses):
                raise RuntimeError(f"Spring에 서로 다른 네 Pi 세션이 있어야 합니다. 관측: {sessions}")
            json_file(output / "integrated-checkpoint.json", {"phase": "all_connected", "sessions": sessions})

            inputs = []
            command = press(lab, 1, inputs)
            json_file(output / "button-inputs.json", inputs)
            observed(lab, light_events, lambda events: {"event": "ack_held", "commandId": command} in events,
                     "조명 적용 응답 보류")
            if pending() != [command]:
                raise RuntimeError("두 번째 센서 입력 전에 조명 명령이 응답을 기다려야 합니다.")

            receipts = send_round(lab, streams, 2)
            after = spring_json(lab, "/home/data")
            expected_snapshot = {**before, **SAMPLES["air"][1], **SAMPLES["fire"][1]}
            if after != expected_snapshot or spring_json(lab, "/pilab/session-identities") != sessions:
                raise RuntimeError(f"센서 갱신 후 최신값 또는 세션이 예상과 다릅니다. 관측: {after}")
            spring_held = lab.logs("spring")
            (output / "spring-before-ack-release.log").write_text(spring_held)
            verify_held(command, spring_held, gpio_states(light_events()))
            pending_with_samples = pending()
            if pending_with_samples != [command]:
                raise RuntimeError("센서 저장 직후에도 조명 명령이 한 건 대기 중이어야 합니다.")
            json_file(output / "integrated-checkpoint.json", {
                "phase": "samples_saved_while_light_pending", "pending": pending_with_samples,
            })

            lab.run("exec", "-T", "light", "python", "-c", RELEASE)
            spring = observed(lab, lambda: lab.logs("spring"),
                              lambda log: ("applied", command, "true", "CONFIRMED") in COMMAND.findall(log),
                              "한 번의 조명 적용 응답")
            gpio = [row for row in light_events() if row["event"] == "gpio" and row["offset"] == 18]
            pending_after = pending()
            validate_light(command, COMMAND.findall(spring), gpio, pending_after)
            if spring_samples(lab) != receipts:
                raise RuntimeError("주입한 네 건 외에 센서 수신 기록이 발견됐습니다.")

        lab.run("stop", "main", "light", timeout=30)
        history = verify_history(lab, output, receipts, recreate=False)
        summary = {
            "status": "passed", "sessions": {role: sessions[address] for role, address in addresses.items()},
            "before": before, "after": after, "sensor_receipts": receipts, "sqlite_history": history,
            "light": {"command_id": command, "pending_during_sensor_samples": pending_with_samples,
                      "gpio_writes": gpio, "pending_after_ack": pending_after},
        }
        json_file(output / "summary.json", summary)
    json_file(output / "integrated-checkpoint.json", {"phase": "completed"})
    lab.progress(5, "passed")
    print(f"고도화 네 Pi 통합 통과 · {output / 'summary.json'}", flush=True)
    return summary
