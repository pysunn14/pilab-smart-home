"""Verify ordered sensor receipts and SQLite history after Spring replacement."""

from contextlib import closing
import json
import re
import sqlite3
import subprocess

from .receiver_preview import TELEMETRY_ROLES
from .smoke import json_file, open_run, role_ips


SAMPLES = {
    "air": [{"dust": 23.5}, {"dust": 41.2, "temperature": 24.1, "humidity": 42}],
    "fire": [{"infra": 951, "gas": 40}, {"infra": 812, "gas": 12}],
}


def spring_json(lab, path):
    url = f"http://127.0.0.1:8080{path}"
    return json.loads(lab.run("exec", "-T", "spring", "curl", "-fsS", url))


def spring_samples(lab):
    marker = '{"event":"sensor_sample",'
    return [json.loads(line[line.index(marker):]) for line in lab.logs("spring").splitlines() if marker in line]


def reconnect_idle_sensor(lab, address, first):
    url = "http://127.0.0.1:8080/pilab/sensor-disconnect"
    row = json.loads(lab.run(
        "exec", "-T", "spring", "curl", "-fsS", "--data-urlencode", f"nodeId={first['sample']['nodeId']}",
        "--data-urlencode", f"sessionId={first['session_id']}", url,
    ))
    if row != {"closed": True}:
        raise RuntimeError(f"센서의 현재 세션을 닫지 못했습니다. 관측: {row}")
    replacement = lab.wait(lambda: spring_json(lab, "/pilab/session-identities"),
                           lambda sessions: set(sessions) == {address} and sessions[address] != first["session_id"],
                           "다음 센서 입력 전 WebSocket 재접속", timeout=10)
    if spring_samples(lab) != [first["sample"]]:
        raise RuntimeError("두 번째 입력을 보내기 전에 새 센서 샘플이 기록됐습니다.")
    return replacement[address]


def verify_history(lab, output, receipts=None, *, recreate=True):
    if recreate:
        lab.log_command("spring-restart.log", "up", "-d", "--no-build", "--force-recreate", "--wait", "spring")
    lab.run("stop", "spring", timeout=30)
    if receipts is None:
        receipts = spring_samples(lab)
    database = output / "sensor-samples.sqlite"
    lab.run("cp", f"spring:{lab.env['PILAB_SENSOR_DB_PATH']}", str(database))
    # Spring is stopped so the copied file includes the data from the closed WAL connection.
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
            rows = connection.execute("""
                SELECT node_id, run_id, seq, received_at, values_json
                FROM sensor_samples ORDER BY node_id, run_id, seq
            """).fetchall()
    except sqlite3.Error as error:
        raise RuntimeError("복사한 SQLite 센서 이력을 읽을 수 없습니다.") from error
    history = [{"nodeId": node, "runId": run, "seq": int(seq), "receivedAt": received_at, "values": json.loads(values)}
               for node, run, seq, received_at, values in rows]
    ordered_receipts = sorted(receipts, key=lambda row: (row["nodeId"], row["runId"], row["seq"]))
    expected = [{key: value for key, value in receipt.items() if key != "event"} for receipt in ordered_receipts]
    if history != expected:
        raise RuntimeError(f"SQLite 센서 이력이 수신 로그와 일치하지 않습니다. 관측: {history}")
    return history


def observe_sample(lab, sender, before, expected_sample, address, log_name, *, receipt_count=None):
    if sender.poll() is not None:
        raise RuntimeError(f"장치가 다음 센서값을 받기 전에 종료됐습니다. {log_name}을 확인하세요.")
    sample = expected_sample["values"]
    line = " ".join(str(value) for value in sample.values())
    sender.stdin.write(line + "\n")
    sender.stdin.flush()
    expected = {**before, **sample}
    state = lab.wait(lambda: spring_json(lab, "/home/data"), lambda data: data == expected,
                     f"Spring의 센서값 {sample} 반영", timeout=8)
    sessions = spring_json(lab, "/pilab/session-identities")
    if set(sessions) != {address}:
        raise RuntimeError(f"{address}의 센서 세션 한 개를 기대했습니다. 관측: {sessions}")

    sequence = expected_sample["seq"]
    count = sequence if receipt_count is None else receipt_count
    receipts = lab.wait(lambda: spring_samples(lab), lambda rows: len(rows) >= count,
                        f"Spring 로그의 센서 샘플 {sequence}번", timeout=8)
    receipt = receipts[-1]
    if len(receipts) != count or any(receipt.get(key) != value for key, value in expected_sample.items()):
        raise RuntimeError(f"센서 샘플 ID 또는 값이 예상과 다릅니다. 관측: {receipts}")
    if not re.fullmatch(r"[0-9a-f]{32}", receipt.get("runId", "")) or not receipt.get("receivedAt"):
        raise RuntimeError(f"센서 샘플의 실행 ID 또는 수신 시각이 유효하지 않습니다. 관측: {receipt}")
    return {"snapshot": state, "session_id": sessions[address], "sample": receipt}


def run(repo, output, prefix, profile, *, idle_reconnect=False):
    role = TELEMETRY_ROLES[profile]
    samples = SAMPLES[role]
    address = role_ips(prefix)[role]
    log_name = f"{role}-send.log"
    with open_run(repo, output, prefix, profile) as (lab, _):
        lab.progress(5)
        before = spring_json(lab, "/home/data")
        json_file(output / "before.json", before)
        json_file(output / "input.json", samples)
        if any(all(before.get(key) == value for key, value in sample.items()) for sample in samples):
            raise RuntimeError("입력을 보내기 전에 Spring에 같은 센서값이 이미 기록돼 있습니다.")

        command = [*lab.command, "run", "--rm", "--no-deps", "-T", role, "--stdin"]
        observations = []
        replacement = None
        with (output / log_name).open("w") as log, subprocess.Popen(
            command, env=lab.env, text=True, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
        ) as sender:
            try:
                for sequence, sample in enumerate(samples, 1):
                    expected_sample = {"nodeId": f"{role}pi", "seq": sequence, "values": sample}
                    observations.append(observe_sample(lab, sender, before, expected_sample, address, log_name))
                    json_file(output / "observations.json", observations)
                    if sequence == 1 and idle_reconnect:
                        replacement = reconnect_idle_sensor(lab, address, observations[0])
                first, second = observations
                if second["session_id"] != (replacement or first["session_id"]):
                    raise RuntimeError(f"두 번째 센서 샘플의 세션이 예상과 다릅니다. 관측: {observations}")
                sender.stdin.close()
                if sender.wait(timeout=10) != 0:
                    raise RuntimeError(f"입력이 끝난 뒤 장치 실행에 실패했습니다. {log_name}을 확인하세요.")
            finally:
                if sender.poll() is None:
                    sender.kill()

        lab.wait(lambda: spring_json(lab, "/pilab/session-identities"), lambda sessions: not sessions,
                 "입력 종료 뒤 장치 세션 정리", timeout=8)
        receipts = [row["sample"] for row in observations]
        if len({row["runId"] for row in receipts}) != 1 or spring_samples(lab) != receipts:
            raise RuntimeError(f"센서 샘플의 실행 ID 또는 수신 건수가 달라졌습니다. 관측: {receipts}")
        history = verify_history(lab, output, receipts)
        json_file(output / "summary.json", {
            "status": "passed", "input": samples, "before": before, "after": observations[-1]["snapshot"],
            "observations": observations, "history_after_spring_recreate": history,
            "idle_reconnect": idle_reconnect,
        })
    lab.progress(5, "passed")
    print(f"{profile} 반복 송신 및 SQLite 이력 보존 통과 · {output / 'summary.json'}", flush=True)
