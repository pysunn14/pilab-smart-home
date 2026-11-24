"""Verify that LightPi's delayed application reply holds only the next transmission."""

import re

from .receiver_preview import LIGHT_ACK_DELAY_PROFILE, main_events, node_command
from .smoke import json_file, json_lines, open_run


OBSERVATION_SECONDS = 0.5
COMMAND = re.compile(
    r'light_command_(sent|applied) commandId="(mainpi/[0-9a-f]{32}/[0-9]+)" on=(true|false)'
    r'(?: result=([A-Z_]+))?$', re.MULTILINE,
)
RELEASE = 'import os; fd = os.open("/board/ack-release", os.O_WRONLY | os.O_NONBLOCK); os.write(fd, b"1"); os.close(fd)'


def verify_held(first, spring_log, gpio):
    events = COMMAND.findall(spring_log)
    if events != [("sent", first, "true", "")] or gpio != [True]:
        raise RuntimeError(f"첫 응답 보류 중 전송 또는 출력 순서가 달라졌습니다. Spring {events}, GPIO 18 {gpio}")


def gpio_states(light_events):
    return [event["on"] for event in light_events if event["event"] == "gpio" and event["offset"] == 18]


def observed(lab, read, predicate, description, timeout=8):
    def checked():
        lab.check_processes()
        return read()

    return lab.wait(checked, predicate, description, timeout=timeout)


def press(lab, sequence, inputs):
    admitted = re.compile(
        rf'button_admission nodeId="mainpi" runId=([0-9a-f]{{32}}) seq={sequence} result=ACCEPTED$', re.MULTILINE,
    )
    inputs.append(json_lines(node_command(lab, "edge", "0"))[0])
    log = observed(lab, lambda: lab.logs("spring"), admitted.search, f"버튼 #{sequence} 접수")
    inputs.append(json_lines(node_command(lab, "edge", "1"))[0])
    observed(lab, lambda: main_events(lab),
             lambda events: sum(event["event"] == "button_released" for event in events) == sequence,
             f"버튼 #{sequence} 떼기")
    return f"mainpi/{admitted.search(log).group(1)}/{sequence}"


def run(repo, output, prefix):
    with open_run(repo, output, prefix, LIGHT_ACK_DELAY_PROFILE) as (lab, _):
        lab.progress(5)
        inputs = []

        def light_events():
            return json_lines(lab.logs("light"))

        def spring_log():
            return lab.logs("spring")

        try:
            observed(lab, spring_log, lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                     "LightPi의 초기 꺼짐 상태")
            first = press(lab, 1, inputs)
            observed(lab, light_events, lambda events: {"event": "ack_held", "commandId": first} in events,
                     "첫 명령 적용 응답 보류")
            json_file(output / "ack-delay-checkpoint.json", {"phase": "first_ack_held", "command_id": first})

            second = press(lab, 2, inputs)
            lab.cancel.wait(OBSERVATION_SECONDS)
            lab.check_processes()
            held_log = spring_log()
            held_gpio = gpio_states(light_events())
            (output / "spring-before-release.log").write_text(held_log)
            verify_held(first, held_log, held_gpio)
            json_file(output / "ack-delay-checkpoint.json", {
                "phase": "second_admitted", "command_id": second, "gpio_18_writes": held_gpio,
            })

            lab.run("exec", "-T", "light", "python", "-c", RELEASE)
            expected_first = ("applied", first, "true", "CONFIRMED")
            expected_second = ("applied", second, "false", "CONFIRMED")
            second_send = ("sent", second, "false", "")
            final_log = observed(lab, spring_log,
                                 lambda log: {second_send, expected_second} <= set(COMMAND.findall(log)),
                                 "두 번째 명령의 적용 응답")
            events = COMMAND.findall(final_log)
            sent = [event for event in events if event[0] == "sent"]
            applied = [event for event in events if event[0] == "applied"]
            final_gpio = gpio_states(light_events())
            if (sent != [("sent", first, "true", ""), second_send] or applied != [expected_first, expected_second]
                    or events.index(expected_first) >= events.index(second_send) or final_gpio != [True, False]):
                raise RuntimeError(f"응답 해제 후 순서가 예상과 다릅니다. Spring {events}, GPIO 18 {final_gpio}")
        finally:
            json_file(output / "inputs.json", inputs)

    summary = {
        "test_case": "TC-01b", "status": "passed", "gpio_18_states": [False, *final_gpio],
        "admitted_before_release": [first, second], "sent_before_release": [first],
        "confirmed_after_release": [first, second], "observation_seconds": OBSERVATION_SECONDS,
    }
    json_file(output / "summary.json", summary)
    json_file(output / "ack-delay-checkpoint.json", {"phase": "completed"})
    lab.progress(5, "passed")
    print(f"TC-01b 통과 · {output / 'summary.json'}", flush=True)
    return summary
