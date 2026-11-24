"""Discard LightPi application replies after GPIO application and observe Spring."""

import json
import re

from .light_ack_delay import COMMAND, gpio_states, observed, press
from .receiver_preview import LIGHT_ACK_RETRY_PROFILE
from .smoke import json_file, json_lines, open_run


def spring_probe(lab, endpoint, *options):
    return json.loads(lab.run("exec", "-T", "spring", "curl", "-fsS", *options,
                              f"http://127.0.0.1:8080/pilab/{endpoint}"))


def recover_queue(lab, prefix, first, second, sent_before):
    light_ip = f"{prefix}.14"
    session = spring_probe(lab, "session-identities")[light_ip]
    if spring_probe(lab, "light-state-query", "-X", "POST") != {"requested": True}:
        raise RuntimeError("Spring이 보류 중인 명령의 상태 조회를 접수하지 않았습니다.")

    snapshot = f'light_state_snapshot queryId="{first}" lastAppliedCommandId="{first}" on=true result=RECONCILED'
    query = f'light_state_query_sent queryId="{first}"'
    send = ("sent", second, "false", "")
    reply = ("applied", second, "false", "CONFIRMED")

    def finished(log):
        return {send, reply} <= set(COMMAND.findall(log))

    log = observed(lab, lambda: lab.logs("spring"), finished, "상태 조회 이후 두 번째 명령 적용")
    events = COMMAND.findall(log)
    sent = [event for event in events if event[0] == "sent"]
    applied = [event for event in events if event[0] == "applied"]
    gpio = gpio_states(json_lines(lab.logs("light")))
    pending = spring_probe(lab, "light-pending")
    same_session = session == spring_probe(lab, "session-identities").get(light_ip)
    second_send = f'light_command_sent commandId="{second}" on=false'
    if (log.count(query) != 1 or log.count(snapshot) != 1 or log.index(snapshot) >= log.index(second_send)
            or sent != [*sent_before, send] or applied != [reply] or gpio != [True, False]
            or pending or not same_session):
        raise RuntimeError(f"상태 조회 복구 결과 불일치. Spring {events}, GPIO 18 {gpio}, "
                           f"대기 명령 {pending}, 동일 연결 {same_session}")
    return {
        "reconciled_command_id": first,
        "confirmed_command_id": second,
        "gpio_18_writes": gpio,
        "pending_command_ids": pending,
        "same_session": same_session,
    }


def run(repo, output, prefix, drop_count, timeout_ms, *, recover=False):
    with open_run(repo, output, prefix, LIGHT_ACK_RETRY_PROFILE,
                  ack_timeout_ms=timeout_ms, drop_acks=drop_count) as (lab, _):
        lab.progress(5)
        inputs = []

        def spring_log():
            return lab.logs("spring")

        try:
            observed(lab, spring_log, lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                     "LightPi의 초기 꺼짐 상태")
            first = press(lab, 1, inputs)
            second = press(lab, 2, inputs)
            json_file(output / "ack-retry-checkpoint.json", {
                "phase": "both_admitted", "command_ids": [first, second],
            })

            suspended = f'light_dispatch_suspended reason=ACK_TIMEOUT commandId="{first}" application=UNKNOWN'
            second_send = ("sent", second, "false", "")
            first_reply = ("applied", first, "true", "CONFIRMED")
            second_reply = ("applied", second, "false", "CONFIRMED")
            if drop_count == 1:
                observed(lab, spring_log, lambda log: {second_send, second_reply} <= set(COMMAND.findall(log)),
                         "재전송 이후 두 명령의 처리", timeout=8 + timeout_ms / 1000)
            else:
                observed(lab, spring_log, lambda log: suspended in log,
                         "두 번째 타임아웃 후 처리 보류", timeout=8 + 2 * timeout_ms / 1000)
                # Watch one more full retry interval for an unwanted third send.
                lab.cancel.wait(timeout_ms / 1000)
                lab.check_processes()

            log = spring_log()
            events = COMMAND.findall(log)
            sent = [event for event in events if event[0] == "sent"]
            applied = [event for event in events if event[0] == "applied"]
            retries = re.findall(r'light_command_retry commandId="([^"]+)"$', log, re.MULTILINE)
            light_events = json_lines(lab.logs("light"))
            dropped = [event["commandId"] for event in light_events if event["event"] == "ack_dropped"]
            gpio = gpio_states(light_events)
            pending = spring_probe(lab, "light-pending")

            expected_sends = [("sent", first, "true", "")] * 2
            if drop_count == 1:
                correct = (sent == [*expected_sends, second_send] and applied == [first_reply, second_reply]
                           and events.index(first_reply) < events.index(second_send)
                           and suspended not in log and gpio == [True, False] and pending == [])
            else:
                second_admission = f"runId={second.split('/')[1]} seq=2 result=ACCEPTED"
                correct = (sent == expected_sends and not applied and gpio == [True]
                           and log.index(second_admission) < log.index(suspended) and pending == [first, second])
            if not correct or retries != [first] or dropped != [first] * drop_count:
                raise RuntimeError(f"재전송 결과 불일치. Spring {events}, 재전송 {retries}, "
                                   f"버린 응답 {dropped}, GPIO 18 {gpio}, 대기 명령 {pending}")
            if recover:
                json_file(output / "ack-retry-checkpoint.json", {
                    "phase": "before_state_query", "pending_command_ids": pending,
                })
                recovery = recover_queue(lab, prefix, first, second, sent)
        finally:
            json_file(output / "inputs.json", inputs)

    summary = {
        "status": "passed",
        "timeout_ms": timeout_ms,
        "sent_command_ids": [event[1] for event in sent],
        "dropped_ack_ids": dropped,
        "confirmed_command_ids": [event[1] for event in applied],
        "gpio_18_writes": gpio,
        "pending_command_ids": pending,
        "suspended_command_id": first if drop_count == 2 else None,
    }
    if recover:
        summary["recovery"] = recovery
    json_file(output / "summary.json", summary)
    json_file(output / "ack-retry-checkpoint.json", {"phase": "completed"})
    lab.progress(5, "passed")
    print(f"LightPi 적용 응답 유실 {drop_count}회 검증 통과 · {output / 'summary.json'}", flush=True)
    return summary
