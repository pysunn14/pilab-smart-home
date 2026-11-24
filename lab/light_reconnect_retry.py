"""Reject real LightPi WebSocket upgrades and verify the reconnection limit."""

import json
import re

from .light_ack_delay import COMMAND, RELEASE, gpio_states, observed, press
from .light_ack_retry import spring_probe
from .light_reconnect import close_session, verify_reconnect
from .receiver_preview import LIGHT_RECONNECT_PROFILE
from .smoke import json_file, json_lines, open_run, role_ips


def light_container(lab):
    output = lab.run("ps", "--all", "--format", "json", "light")
    rows = json.loads(output) if output.startswith("[") else [json.loads(line) for line in output.splitlines()]
    return next(row for row in rows if row["Service"] == "light")


def run(repo, output, prefix, rejected):
    with open_run(repo, output, prefix, LIGHT_RECONNECT_PROFILE) as (lab, _):
        lab.progress(5)
        inputs = []
        light_ip = role_ips(prefix)["light"]

        def spring_log():
            return lab.logs("spring")

        def light_events():
            return json_lines(lab.logs("light"))

        def light_session():
            return spring_probe(lab, "session-identities").get(light_ip)

        try:
            observed(lab, spring_log, lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                     "LightPi의 초기 상태")
            session_before = observed(lab, light_session, bool, "기존 LightPi 세션")
            first = press(lab, 1, inputs)
            observed(lab, light_events, lambda events: {"event": "ack_held", "commandId": first} in events,
                     "첫 명령의 GPIO 적용")
            lab.run("exec", "-T", "light", "python", "-c", RELEASE)
            applied_on = ("applied", first, "true", "CONFIRMED")
            observed(lab, spring_log, lambda log: applied_on in COMMAND.findall(log), "첫 명령의 적용 응답")
            if spring_probe(lab, "light-connection-fault", "-X", "POST",
                            "--data-urlencode", f"count={rejected}") != {"armed": True}:
                raise RuntimeError("LightPi 연결 거절을 설정하지 못했습니다.")
            close_session(lab, session_before, [])
            json_file(output / "reconnect-checkpoint.json", {"phase": "socket_closed", "reject_attempts": rejected})

            if rejected == 3:
                exited = lab.wait(lambda: light_container(lab), lambda row: row["State"] == "exited",
                                  "LightPi의 재접속 한도 도달 후 종료", timeout=12)
                session_after = light_session()
                if (exited["ExitCode"] != 1 or session_after is not None or gpio_states(light_events()) != [True]
                        or spring_probe(lab, "light-pending") or COMMAND.findall(spring_log()) != [
                            ("sent", first, "true", ""), applied_on]):
                    raise RuntimeError(f"재접속 한도 도달 후 상태가 예상과 다릅니다. {exited}")
                result = {"exit_code": exited["ExitCode"], "gpio_18_writes": [True]}
            else:
                session_after = observed(lab, light_session, lambda value: bool(value) and value != session_before,
                                         "연속 연결 거절 이후 새 LightPi 세션", timeout=12)
                observed(lab, spring_log, lambda log: "light_state_reported on=true result=RECONNECTED" in log,
                         "Spring이 새 세션의 GPIO 상태를 확인")
                second = press(lab, 2, inputs)
                result = verify_reconnect(lab, light_session, session_after, first, second, pending=False)

            events = light_events()
            attempts = [event["attempt"] for event in events if event["event"] == "reconnect_attempt"]
            failures = [event["attempt"] for event in events if event["event"] == "reconnect_failed"]
            ready = [event for event in events if event["event"] == "ready"]
            exhausted = sum(event["event"] == "reconnect_exhausted" for event in events)
            denials = re.findall(r"lab_light_handshake_rejected remaining=(\d+)$", spring_log(), re.MULTILINE)
            expected_attempts = list(range(1, min(3, rejected + 1) + 1))
            if (attempts != expected_attempts or failures != list(range(1, rejected + 1))
                    or denials != [str(count) for count in reversed(range(rejected))]
                    or exhausted != int(rejected == 3)
                    or len(ready) != (1 if rejected == 3 else 2)
                    or len({event["runId"] for event in ready}) != 1):
                raise RuntimeError(f"감지한 재접속 시도 {attempts}, 실패 {failures}, Spring 거절 {denials}")
        finally:
            json_file(output / "inputs.json", inputs)

    summary = {
        **result, "status": "passed", "recovered": rejected < 3,
        "session_before": session_before, "session_after": session_after,
        "reconnect_attempts": attempts, "failed_attempts": failures, "handshakes_rejected": len(denials),
    }
    json_file(output / "summary.json", summary)
    json_file(output / "reconnect-checkpoint.json", {"phase": "completed"})
    lab.progress(5, "passed")
    print(f"LightPi 재접속 한도 검증 통과 · {output / 'summary.json'}", flush=True)
    return summary
