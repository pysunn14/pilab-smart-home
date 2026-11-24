"""Verify the FirePi alarm output while the FirePi sensor stream stays open."""

import json
import subprocess
from uuid import UUID

from .receiver_preview import FIRE_ALARM_PROFILE
from .smoke import json_file, open_run, role_ips
from .telemetry_smoke import observe_sample, spring_json, spring_samples, verify_history


def set_alarm(lab, on):
    return json.loads(lab.run(
        "exec", "-T", "spring", "curl", "-fsS", "--max-time", "6",
        "--data-urlencode", f"on={str(on).lower()}", "http://127.0.0.1:8080/pilab/fire-alarm",
    ))


def run(repo, output, prefix):
    with open_run(repo, output, prefix, FIRE_ALARM_PROFILE) as (lab, _):
        lab.progress(5)
        virtual = output / "virtual-alarm"
        virtual.mkdir()
        state = virtual / "alarm.txt"
        logfile = "firepi.log"
        command = [*lab.command, "run", "--rm", "--no-deps", "-T", "--volume", f"{virtual}:/alarm",
                   "fire", "--stdin", "--alarm-file", "/alarm/alarm.txt"]
        before = spring_json(lab, "/home/data")
        address = role_ips(prefix)["fire"]
        with (output / logfile).open("w") as log, subprocess.Popen(
            command, env=lab.env, stdout=log, stderr=subprocess.STDOUT, text=True, stdin=subprocess.PIPE,
        ) as fire:
            try:
                initial = observe_sample(lab, fire, before, {
                    "nodeId": "firepi", "seq": 1, "values": {"infra": 951, "gas": 40},
                }, address, logfile)
                if state.read_text() != "0\n":
                    raise RuntimeError("FirePi 가상 경고등이 꺼진 상태에서 시작해야 합니다.")

                receipts = []
                for on in (True, False):
                    receipt = set_alarm(lab, on)
                    command_id = receipt.get("commandId", "")
                    UUID(command_id)
                    if (receipt.get("on") is not on or receipt.get("status") != "applied"
                            or state.read_text() != f"{int(on)}\n"):
                        raise RuntimeError(f"Spring의 응답과 가상 경고등 출력이 다릅니다. 응답 {receipt}, 출력 {state.read_text()}")
                    receipts.append(receipt)

                final = observe_sample(lab, fire, before, {
                    "nodeId": "firepi", "seq": 2, "values": {"infra": 812, "gas": 12},
                }, address, logfile)
                if (initial["session_id"] != final["session_id"]
                        or initial["sample"]["runId"] != final["sample"]["runId"]):
                    raise RuntimeError("경고등 명령 전후 FirePi 센서 연결이 달라졌습니다.")
                fire.stdin.close()
                if fire.wait(timeout=10) != 0:
                    raise RuntimeError(f"FirePi가 정상 종료되지 않았습니다. {logfile}을 확인하세요.")
            finally:
                if fire.poll() is None:
                    fire.kill()

        events = [json.loads(line) for line in (output / logfile).read_text().splitlines()
                  if line.startswith('{"event":"fire_alarm_applied",')]
        expected = [{"event": "fire_alarm_applied", "commandId": row["commandId"], "on": row["on"]}
                    for row in receipts]
        if events != expected or len({row["commandId"] for row in receipts}) != 2:
            raise RuntimeError(f"두 명령과 실제 가상 경고등 출력이 일치해야 합니다. 관측 {events}")
        history = verify_history(lab, output, spring_samples(lab), recreate=False)
        json_file(output / "summary.json", {
            "status": "passed", "alarm_states": [False, *(event["on"] for event in events)],
            "receipts": receipts, "applications": events, "sensor_history": history,
        })
    lab.progress(5, "passed")
    print(f"FirePi 가상 경고등 꺼짐 → 켜짐 → 꺼짐 확인 · {output / 'summary.json'}", flush=True)
