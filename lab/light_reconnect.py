"""Observe LightPi reconnection with an empty or a pending command queue."""

from .light_ack_delay import COMMAND, RELEASE, gpio_states, observed, press
from .light_ack_retry import spring_probe
from .receiver_preview import LIGHT_RECONNECT_PROFILE
from .smoke import json_file, json_lines, open_run


def close_session(lab, session_id, expected):
    if spring_probe(lab, "light-pending") != expected:
        raise RuntimeError(f"연결을 닫기 전 Spring 큐에 필요한 명령 순서 {expected}")
    options = ["-X", "POST", "--data-urlencode", f"sessionId={session_id}"]
    for command_id in expected:
        options.extend(["--data-urlencode", f"pendingCommandId={command_id}"])
    if spring_probe(lab, "light-disconnect", *options) != {"closed": True}:
        raise RuntimeError("예상한 큐와 세션으로 기존 LightPi 연결을 닫지 못했습니다.")


def run(repo, output, prefix, *, pending=False, undelivered=False, restart=False):
    command_lost = undelivered or restart
    with open_run(repo, output, prefix, LIGHT_RECONNECT_PROFILE, drop_acks=int(pending),
                  drop_first_light_command=command_lost) as (lab, _):
        lab.progress(5)
        inputs = []
        light_ip = f"{prefix}.14"

        def light_events():
            return json_lines(lab.logs("light"))

        def spring_log():
            return lab.logs("spring")

        def light_session():
            return spring_probe(lab, "session-identities").get(light_ip)

        try:
            observed(lab, spring_log, lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                     "LightPi의 초기 상태")
            session_before = observed(lab, light_session, bool, "기존 LightPi 세션")
            first = press(lab, 1, inputs)
            applied_on = ("applied", first, "true", "CONFIRMED")
            if command_lost:
                dropped = f'lab_light_command_dropped commandId="{first}"'
                observed(lab, spring_log, lambda log: dropped in log, "켜기 명령의 전송 전 유실")
            elif pending:
                observed(lab, light_events, lambda events: {"event": "ack_dropped", "commandId": first} in events,
                         "켜기 명령 적용 후 응답 유실")
            else:
                observed(lab, light_events, lambda events: {"event": "ack_held", "commandId": first} in events,
                         "첫 명령 GPIO 적용")
                lab.run("exec", "-T", "light", "python", "-c", RELEASE)
                observed(lab, spring_log, lambda log: applied_on in COMMAND.findall(log), "켜기 명령의 적용 응답")
            queued = []
            if pending or command_lost:
                second = press(lab, 2, inputs)
                queued = [first, second]
            expected_gpio = [] if command_lost else [True]
            if gpio_states(light_events()) != expected_gpio:
                raise RuntimeError(f"연결 종료 전 가상 GPIO 쓰기가 예상과 다릅니다. 예상 {expected_gpio}")
            if restart:
                if spring_probe(lab, "light-pending") != queued or light_session() != session_before:
                    raise RuntimeError("LightPi를 재시작하기 전 대기 명령과 세션이 예상과 달라졌습니다.")
                lab.log_command("restart.log", "restart", "light")
            else:
                close_session(lab, session_before, queued)
            json_file(output / "reconnect-checkpoint.json", {
                "phase": "restarted" if restart else "closed",
                "session": session_before, "pending_before_disconnect": queued,
            })

            session_after = observed(lab, light_session, lambda value: bool(value) and value != session_before,
                                     "새 LightPi 세션")
            expected_report = "RECOVERING" if queued else "RECONNECTED"
            level = "false" if command_lost else "true"
            observed(lab, spring_log, lambda log: f"light_state_reported on={level} result={expected_report}" in log,
                     "새 LightPi 세션의 상태 보고에 대한 Spring 응답")
            ready = observed(lab, lambda: [event for event in light_events() if event["event"] == "ready"],
                             lambda events: len(events) >= 2, "재연결한 LightPi의 준비 기록")
            if len(ready) != 2 or [event["on"] for event in ready] != [False, not command_lost]:
                raise RuntimeError(f"재연결에서 LightPi 프로세스 또는 출력 상태가 달라졌습니다. {ready}")
            run_before, run_after = (event.get("runId") for event in ready)
            if (not run_before or not run_after or (run_before != run_after) != restart
                    or (not restart and ready[0]["pid"] != ready[1]["pid"])):
                raise RuntimeError(f"기대한 LightPi 실행 기록과 다릅니다. {ready}")

            if restart:
                result = verify_restart(lab, light_session, session_after, first, second)
            elif undelivered:
                result = verify_undelivered(lab, light_session, session_after, first, second)
            else:
                if not pending:
                    second = press(lab, 2, inputs)
                result = verify_reconnect(lab, light_session, session_after, first, second, pending=pending)
        finally:
            json_file(output / "inputs.json", inputs)

    summary = {
        **result, "session_before": session_before, "session_after": session_after,
        "light_pid": ready[0]["pid"], "pending_before_disconnect": queued,
        "light_run_id_before": run_before, "light_run_id_after": run_after,
    }
    json_file(output / "summary.json", summary)
    json_file(output / "reconnect-checkpoint.json", {"phase": "completed"})
    lab.progress(5, result["status"])
    label = "LightPi 재연결 후 큐 복구 검증 통과" if pending else "LightPi 재연결 검증 통과"
    if undelivered:
        label = "미적용 조명 명령 재전송과 큐 복구 검증 통과"
    if restart:
        label = "LightPi 재시작 후 미확인 명령 보류 검증 통과"
    print(f"{label} · {output / 'summary.json'}", flush=True)
    return summary


def verify_restart(lab, light_session, expected_session, first, second):
    snapshot = f'light_state_snapshot queryId="{first}" lastAppliedCommandId=null on=false result=MISMATCH'
    observed(lab, lambda: lab.logs("spring"), lambda log: snapshot in log, "새 실행에서 조명 명령 보류")
    observation_seconds = 2
    lab.cancel.wait(observation_seconds)
    lab.check_processes()
    log = lab.logs("spring")
    events = COMMAND.findall(log)
    gpio = gpio_states(json_lines(lab.logs("light")))
    waiting = spring_probe(lab, "light-pending")
    query = f'light_state_query_sent queryId="{first}"'
    if (events != [("sent", first, "true", "")] or log.count(query) != 1 or log.count(snapshot) != 1
            or "light_command_retry " in log or gpio or waiting != [first, second]
            or light_session() != expected_session):
        raise RuntimeError(f"LightPi 재시작 이후 보류 상태가 예상과 다릅니다. Spring {events}, GPIO 18 {gpio}, 큐 {waiting}")
    return {
        "status": "passed", "observed_state": {"last_applied_command_id": None, "gpio_on": False, "result": "MISMATCH"},
        "pending_command_ids": waiting, "gpio_18_writes": gpio,
        "sender_reported_sent_command_ids": [event[1] for event in events],
        "observation_seconds": observation_seconds,
    }


def verify_undelivered(lab, light_session, expected_session, first, second):
    observed(lab, lambda: json_lines(lab.logs("light")),
             lambda events: {"event": "ack_held", "commandId": first} in events,
             "같은 ID로 재전송한 켜기 명령의 GPIO 적용")
    lab.run("exec", "-T", "light", "python", "-c", RELEASE)
    applied_on = ("applied", first, "true", "CONFIRMED")
    sent_off = ("sent", second, "false", "")
    applied_off = ("applied", second, "false", "CONFIRMED")
    log = observed(lab, lambda: lab.logs("spring"),
                   lambda log: {sent_off, applied_off} <= set(COMMAND.findall(log)),
                   "미적용 켜기 재전송 이후 끄기 적용")
    events = COMMAND.findall(log)
    sent = [event for event in events if event[0] == "sent"]
    applied = [event for event in events if event[0] == "applied"]
    gpio = gpio_states(json_lines(lab.logs("light")))
    waiting = spring_probe(lab, "light-pending")
    snapshot = f'light_state_snapshot queryId="{first}" lastAppliedCommandId=null on=false result=REPLAYING'
    query = f'light_state_query_sent queryId="{first}"'
    dropped = f'lab_light_command_dropped commandId="{first}"'
    retry = f'light_command_retry commandId="{first}"'
    if (sent != [("sent", first, "true", ""), ("sent", first, "true", ""), sent_off]
            or applied != [applied_on, applied_off] or events.index(applied_on) >= events.index(sent_off)
            or any(log.count(marker) != 1 for marker in (query, dropped, snapshot, retry))
            or gpio != [True, False] or waiting or light_session() != expected_session):
        raise RuntimeError(f"미적용 명령 복구 결과가 예상과 다릅니다. Spring {events}, GPIO 18 {gpio}, 큐 {waiting}")
    return {
        "status": "passed", "dropped_before_send_command_id": first, "replayed_command_id": first,
        "observed_state": {"last_applied_command_id": None, "gpio_on": False, "result": "REPLAYING"},
        "gpio_18_writes": gpio, "pending_command_ids": waiting, "confirmed_command_ids": [first, second],
        "sender_reported_sent_command_ids": [event[1] for event in sent],
    }


def verify_reconnect(lab, light_session, expected_session, first, second, *, pending):
    sent_off = ("sent", second, "false", "")
    applied_off = ("applied", second, "false", "CONFIRMED")
    log = observed(lab, lambda: lab.logs("spring"),
                   lambda log: {sent_off, applied_off} <= set(COMMAND.findall(log)),
                   "재연결 이후 끄기 명령의 적용 응답")
    events = COMMAND.findall(log)
    sent = [event for event in events if event[0] == "sent"]
    applied = [event for event in events if event[0] == "applied"]
    gpio = gpio_states(json_lines(lab.logs("light")))
    expected_replies = [applied_off] if pending else [("applied", first, "true", "CONFIRMED"), applied_off]
    waiting = spring_probe(lab, "light-pending")
    if (sent != [("sent", first, "true", ""), sent_off] or applied != expected_replies
            or gpio != [True, False] or waiting or light_session() != expected_session):
        raise RuntimeError(f"재연결 이후 전송과 적용 결과가 예상과 다릅니다. {events}, GPIO 18 {gpio}")
    result = {
        "status": "passed", "gpio_18_writes": gpio, "confirmed_command_ids": [first, second],
        "sent_command_ids": [event[1] for event in sent], "pending_command_ids": waiting,
    }
    if pending:
        query = f'light_state_query_sent queryId="{first}"'
        snapshot = f'light_state_snapshot queryId="{first}" lastAppliedCommandId="{first}" on=true result=RECONCILED'
        second_send = f'light_command_sent commandId="{second}" on=false'
        if log.count(query) != 1 or log.count(snapshot) != 1 or log.index(snapshot) >= log.index(second_send):
            raise RuntimeError("켜기 명령의 상태가 새 연결에서 한 번 확인된 뒤 끄기 명령이 전송돼야 합니다.")
        result["reconciled_command_id"] = first
    return result
