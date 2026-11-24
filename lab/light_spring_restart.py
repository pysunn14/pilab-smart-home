"""Restart Spring and record transport and button recovery separately."""

import json
import subprocess

from .light_ack_delay import COMMAND, RELEASE, gpio_states, observed, press, verify_held
from .light_ack_retry import spring_probe
from .light_reconnect import verify_reconnect
from .receiver_preview import LIGHT_RECONNECT_PROFILE, main_events, spring_receipts
from .smoke import json_file, json_lines, open_run, role_ips


def container_state(lab, role):
    container = lab.run("ps", "--all", "--quiet", role).strip()
    if not container or "\n" in container:
        raise RuntimeError(f"{role} 컨테이너를 한 개 찾지 못했습니다.")
    state = json.loads(subprocess.check_output(
        [lab.command[0], "inspect", "--format", "{{json .State}}", container],
        text=True, env=lab.env, timeout=12,
    ))
    return {key: state[key] for key in ("Status", "StartedAt", "ExitCode", "Health") if key in state}


def run(repo, output, prefix, *, pending=False):
    with open_run(repo, output, prefix, LIGHT_RECONNECT_PROFILE, drop_acks=int(pending),
                  persist_light_queue=pending) as (lab, _):
        lab.progress(5)
        inputs = []
        light_ip = role_ips(prefix)["light"]
        main_ip = role_ips(prefix)["main"]
        summary = {"status": "failed", "lightpi_reconnected": False, "button_recovered": False}

        def spring_log():
            return lab.logs("spring")

        def light_events():
            return json_lines(lab.logs("light"))

        def light_session():
            return spring_probe(lab, "session-identities").get(light_ip)

        def main_session():
            return spring_probe(lab, "session-identities").get(main_ip)

        phase = "before_spring_restart"
        try:
            observed(lab, spring_log, lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                     "LightPi 초기 상태")
            summary["session_before"] = observed(lab, light_session, bool, "이전 Spring의 LightPi 세션")
            summary["main_session_before"] = observed(lab, main_session, bool, "첫 버튼 입력 전 MainPi 세션")
            first = press(lab, 1, inputs)
            queued = []
            if pending:
                observed(lab, light_events, lambda events: {"event": "ack_dropped", "commandId": first} in events,
                         "재시작 전 켜기 적용과 응답 유실")
                queued = [first, press(lab, 2, inputs)]
                verify_held(first, spring_log(), gpio_states(light_events()))
            else:
                observed(lab, light_events, lambda events: {"event": "ack_held", "commandId": first} in events,
                         "재시작 전 켜기 적용")
                lab.run("exec", "-T", "light", "python", "-c", RELEASE)
                observed(lab, spring_log, lambda log: ("applied", first, "true", "CONFIRMED") in COMMAND.findall(log),
                         "재시작 전 켜기 적용 응답")
            if spring_probe(lab, "light-pending") != queued or gpio_states(light_events()) != [True]:
                raise RuntimeError(f"재시작 전 GPIO가 켜져 있고 Spring 큐가 {queued}이어야 합니다.")
            summary["queued_before_restart"] = queued
            before = {role: container_state(lab, role)["StartedAt"] for role in ("spring", "main", "light")}
            summary["started_before"] = before

            phase = "spring_restart"
            lab.log_command("spring-restart.log", "restart", "--no-deps", "spring")
            lab.wait(lambda: container_state(lab, "spring"),
                     lambda state: state["StartedAt"] != before["spring"]
                     and state.get("Health", {}).get("Status") == "healthy",
                     "새 Spring 프로세스의 HTTP 준비", timeout=20)
            after = {role: container_state(lab, role)["StartedAt"] for role in before}
            summary["started_after"] = after
            if any(after[role] != before[role] for role in ("main", "light")):
                raise RuntimeError("Pi 컨테이너가 실험 도중 재시작됐습니다.")
            json_file(output / "restart-checkpoint.json", {"phase": "spring_ready"})

            phase = "lightpi_reconnect"
            session = observed(lab, light_session,
                               lambda value: bool(value) and value != summary["session_before"],
                               "새 Spring의 LightPi 세션", timeout=12)
            expected_report = "RECOVERING" if pending else "INITIALIZED"
            observed(lab, spring_log, lambda log: f"light_state_reported on=true result={expected_report}" in log,
                     f"새 Spring의 LightPi 상태 보고 {expected_report}")
            ready = observed(lab, lambda: [event for event in light_events() if event["event"] == "ready"],
                             lambda events: len(events) >= 2, "동일 LightPi의 재연결 기록")
            attempts = [event["attempt"] for event in light_events() if event["event"] == "reconnect_attempt"]
            if (len(ready) != 2 or ready[0]["runId"] != ready[1]["runId"]
                    or attempts not in ([1], [1, 2], [1, 2, 3])):
                raise RuntimeError(f"LightPi 실행 ID와 접속 횟수가 예상과 다릅니다. {ready}, 시도 {attempts}")
            summary.update(lightpi_reconnected=True, session_after=session, light_run_id=ready[0]["runId"])
            json_file(output / "restart-checkpoint.json", {"phase": "lightpi_reconnected", "session": session})

            phase = "mainpi_reconnect"
            summary["main_session_after"] = observed(
                lab, main_session, lambda value: bool(value) and value != summary["main_session_before"],
                "새 버튼 입력 전 MainPi 재연결",
            )
            if pending:
                phase = "old_queue_after_restart"
                result = verify_reconnect(lab, light_session, session, first, queued[1], pending=True)
                summary["recovered_queued_command_id"] = queued[1]
            else:
                phase = "button_after_restart"
                fresh = press(lab, 2, inputs)
                if first.rsplit("/", 1)[0] != fresh.rsplit("/", 1)[0]:
                    raise RuntimeError("재연결 이후 MainPi 실행 ID가 달라졌습니다.")
                result = verify_reconnect(lab, light_session, session, first, fresh, pending=False)
            summary.update(result, button_recovered=True)
            json_file(output / "restart-checkpoint.json", {"phase": "completed"})
        except (RuntimeError, subprocess.SubprocessError) as error:
            summary["failed_phase"] = phase
            summary["error"] = str(error).split("\n", 1)[0]
            raise
        finally:
            summary["gpio_18_writes"] = gpio_states(light_events())
            summary["reconnect_attempts"] = [event["attempt"] for event in light_events()
                                             if event["event"] == "reconnect_attempt"]
            summary["mainpi_reported_sent_sequences"] = [event["seq"] for event in main_events(lab)
                                                          if event["event"] == "button_tx_sent"]
            summary["spring_received_sequences"] = [item["seq"] for item in spring_receipts(lab)]
            json_file(output / "inputs.json", inputs)
            json_file(output / "summary.json", summary)

    lab.progress(5, "passed")
    print(f"Spring 재시작 이후 버튼 제어 검증 통과 · {output / 'summary.json'}", flush=True)
    return summary
