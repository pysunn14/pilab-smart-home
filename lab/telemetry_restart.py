"""Compare sensor reconnect attempts with a full Spring restart."""

import json
import subprocess
from threading import Thread
from time import monotonic_ns

from .smoke import json_file, open_run, role_ips
from .telemetry_smoke import SAMPLES, TELEMETRY_ROLES, observe_sample, spring_json, spring_samples, verify_history


RETRY_DELAYS = (0, 1000, 2000)


def validate_reconnect(events, outcome, exit_code):
    attempts = [(event["attempt"], event["delay_ms"]) for event in events
                if event["event"] == "reconnect_attempt"]
    recovered = outcome == "recovered"
    expected_attempts = (list(enumerate(RETRY_DELAYS[:len(attempts)], 1)) if recovered
                         else list(enumerate(RETRY_DELAYS, 1)))
    expected_events = [("connection_lost", None)]
    for number, _ in expected_attempts:
        expected_events.append(("reconnect_attempt", number))
        if not recovered or number < len(expected_attempts):
            expected_events.append(("reconnect_failed", number))
    expected_events.append(("reconnected" if recovered else "reconnect_exhausted", None))
    observed_events = [(event["event"], event.get("attempt")) for event in events]
    if (not attempts or attempts != expected_attempts or observed_events != expected_events
            or recovered != (exit_code == 0)):
        raise RuntimeError(f"센서 재접속 로그와 종료 결과가 일치하지 않습니다. {outcome}, {exit_code}, {events}")


def record_sender(sender, log, events):
    for line in sender.stdout:
        observed_ns = monotonic_ns()
        log.write(line)
        if line.startswith('{"event":'):
            events.append({**json.loads(line), "observer": "sender_stdout", "observed_ns": observed_ns})


def mark(timeline, event, **fields):
    timeline.append({"event": event, "observer": "controller", "observed_ns": monotonic_ns(), **fields})


def restart_spring(lab, sender, timeline, hold_down):
    original = lab.run("ps", "-q", "spring").strip()
    mark(timeline, "spring_stop_requested", container_id=original)
    lab.log_command("spring-stop.log", "kill", "-s", "SIGKILL", "spring")
    mark(timeline, "spring_stopped")

    if hold_down:
        exit_code = sender.wait(timeout=20)
        mark(timeline, "sender_exited_during_outage", exit_code=exit_code)

    mark(timeline, "spring_start_requested")
    lab.log_command("spring-restart.log", "up", "-d", "--no-build", "--force-recreate", "--wait", "spring")
    replacement = lab.run("ps", "-q", "spring").strip()
    if not original or not replacement or original == replacement:
        raise RuntimeError("Spring 컨테이너가 교체되지 않았습니다.")
    mark(timeline, "spring_health_ready", container_id=replacement)


def run(repo, output, prefix, profile, *, hold_down=False):
    role = TELEMETRY_ROLES[profile]
    address = role_ips(prefix)[role]
    log_name = f"{role}-send.log"
    first_values, second_values = SAMPLES[role]
    node = f"{role}pi"
    events, timeline = [], []
    with open_run(repo, output, prefix, profile) as (lab, _):
        lab.progress(5)
        before = spring_json(lab, "/home/data")
        command = [*lab.command, "run", "--rm", "--no-deps", "-T", role, "--stdin"]
        with (output / log_name).open("w") as log, subprocess.Popen(
                command, env=lab.env, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT) as sender:
            reader = Thread(target=record_sender, args=(sender, log, events), daemon=True)
            reader.start()
            try:
                first = observe_sample(lab, sender, before, {"nodeId": node, "seq": 1, "values": first_values},
                                       address, log_name)
                observations = [first]
                (output / "spring-before-restart.log").write_text(lab.logs("spring"))
                restart_spring(lab, sender, timeline, hold_down)

                def observe_outcome():
                    code = sender.poll()
                    if code is not None:
                        return {"exit_code": code}
                    new_session = spring_json(lab, "/pilab/session-identities").get(address)
                    return {"session_id": new_session} if new_session and new_session != first["session_id"] else None

                result = lab.wait(observe_outcome, bool, "재접속한 센서 세션 또는 프로세스 종료", timeout=15)
                restarted_snapshot = spring_json(lab, "/home/data")
                if spring_samples(lab):
                    raise RuntimeError("두 번째 입력을 보내기 전에 새 Spring에 센서 샘플이 도착했습니다.")
                if "session_id" in result:
                    if hold_down:
                        raise RuntimeError("재시도 소진 뒤에도 센서가 재접속했습니다.")
                    outcome = "recovered"
                    mark(timeline, "replacement_session_observed", session_id=result["session_id"])
                    second = observe_sample(
                        lab, sender, restarted_snapshot, {"nodeId": node, "seq": 2, "values": second_values},
                        address, log_name, receipt_count=1,
                    )
                    if (second["session_id"] != result["session_id"]
                            or second["sample"]["runId"] != first["sample"]["runId"]):
                        raise RuntimeError("재접속 후 샘플의 세션 또는 실행 ID가 예상과 다릅니다.")
                    observations.append(second)
                    sender.stdin.close()
                    exit_code = sender.wait(timeout=10)
                else:
                    outcome = "retry_exhausted"
                    exit_code = result["exit_code"]
                mark(timeline, "sender_exit_observed", exit_code=exit_code)
            finally:
                if sender.poll() is None:
                    sender.kill()
                    sender.wait(timeout=5)
                reader.join(timeout=5)
                if reader.is_alive():
                    raise RuntimeError("센서 송신 로그 수집기가 종료되지 않았습니다.")

        validate_reconnect(events, outcome, exit_code)
        lab.wait(lambda: spring_json(lab, "/pilab/session-identities"), lambda sessions: not sessions,
                 "센서 세션 정리", timeout=8)
        receipts = [row["sample"] for row in observations]
        history = verify_history(lab, output, receipts, recreate=False)
        ordered = sorted(events + timeline, key=lambda event: event["observed_ns"])
        origin = ordered[0]["observed_ns"]
        for event in ordered:
            event["elapsed_ms"] = (event["observed_ns"] - origin) / 1_000_000
        json_file(output / "summary.json", {
            "status": "passed", "outcome": outcome, "hold_down_until_exhausted": hold_down,
            "clock": "host_monotonic_ns", "timeline": ordered, "observations": observations,
            "history_after_spring_recreate": history,
        })
    lab.progress(5, "passed")
    print(f"{profile} Spring 재시작 결과: {outcome} · {output / 'summary.json'}", flush=True)
