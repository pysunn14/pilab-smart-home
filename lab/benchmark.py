"""Run the legacy button latency and delivery benchmark in an isolated lab."""

import hashlib
import json
import subprocess
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from .benchmark_result import DEADLINE_MS, MS, SLO_TARGETS, classify, summarize
from .smoke import json_file, json_lines, open_run, role_ips


DEFAULT_PREFIX = "172.30.253"
DEFAULT_COUNT = 20
DEFAULT_SEED = 17
WORKFLOW = "button-led"
CLOCK = "lab-linux-monotonic"
PROFILE = {
    "slot_ms": 5_000, "jitter_ms": 750, "pulse_ms": 250, "window_ms": 4_000,
    "lead_ms": 500, "http_interval_ms": 500,
}


def replay(lab, output, profile, *, node_script="node.py", action="benchmark-button"):
    records = []
    command = [*lab.command, "exec", "-T", "main", "python", f"/app/{node_script}",
               action, json.dumps(profile)]
    header = f"GPIO 버튼 · 입력 {profile['count']}건"
    print(header, flush=True)
    with (output / "workload.jsonl").open("w", buffering=1) as raw, \
         (output / "workload.stderr.log").open("w") as err, \
         subprocess.Popen(command, env=lab.env, stdout=subprocess.PIPE, stderr=err, text=True) as process:
        try:
            for line in process.stdout:
                raw.write(line)
                record = json.loads(line)
                records.append(record)
                if record["event"] == "input":
                    number = record["sample"]
                    length = 20 * number // profile["count"]
                    bar = "#" * length + "-" * (20 - length)
                    print(f"[{bar}] {number}/{profile['count']} 입력 주입", flush=True)
                    json_file(output / "benchmark-checkpoint.json",
                              {"phase": "replaying", "injected": number, "scheduled": profile["count"]})
            if process.wait():
                raise RuntimeError("입력 재생 실패. workload.stderr.log를 확인하세요.")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return records


def runtime(lab):
    code = """import json, os, platform
from pathlib import Path

offsets = Path('/proc/self/timens_offsets').read_text().splitlines()
monotonic = next(line.split()[1:] for line in offsets if line.split()[0] == 'monotonic')
print(json.dumps({
    'clock_namespace': os.readlink('/proc/self/ns/time'),
    'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    'monotonic_offset': [int(part) for part in monotonic],
    'kernel': platform.release(),
    'architecture': platform.machine(),
    'python': platform.python_version(),
    'linux_cpus': os.sysconf('SC_NPROCESSORS_ONLN'),
    'linux_memory_bytes': os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE'),
}))"""
    clocks = {role: json.loads(lab.run("exec", "-T", role, "python", "-c", code)) for role in ("main", "light")}
    # A time namespace changes CLOCK_MONOTONIC by its offset from the kernel clock.
    keys = {(clock["boot_id"], tuple(clock["monotonic_offset"])) for clock in clocks.values()}
    if len(keys) != 1:
        raise RuntimeError("입력과 출력 컨테이너의 Linux 부팅 ID 또는 단조 시계 오프셋이 다릅니다.")
    return {"clock_sources": clocks}


def write_records(path, records):
    with path.open("w") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def trace(run_id, samples, releases, detected, unassigned):
    starts = [sample["input"]["timestamp_ns"] for sample in samples]
    detections = [[] for _ in samples]
    extra_detections = []
    for point in detected:
        index = bisect_right(starts, point["timestamp_ns"]) - 1
        release = releases.get(samples[index]["input"]["sample"]) if index >= 0 else None
        if release and point["timestamp_ns"] <= release["timestamp_ns"]:
            detections[index].append(point)
        else:
            extra_detections.append(point)

    def event(stage, role, timestamp_ns, event_id=None, status="ok", **fields):
        return {"run_id": run_id, "event_id": event_id, "node_id": role, "workflow": WORKFLOW,
                "stage": stage, "timestamp_ns": timestamp_ns, "clock_id": CLOCK if timestamp_ns is not None else None,
                "status": status, "expected_output": None, "observed_output": None, **fields}

    entries = []
    for sample, observed in zip(samples, detections):
        item = sample["input"]
        index = item["sample"]
        event_id = f"button-{index:06d}"
        sample["event_id"] = event_id
        sample["detected_ns"] = [point["timestamp_ns"] for point in observed]
        sample["release_ns"] = releases.get(index, {}).get("timestamp_ns")
        entries.append(event("INPUT", "main", item["timestamp_ns"], event_id,
                             scheduled_ns=item["scheduled_ns"], expected_output=sample["expected_output"]))
        if sample["release_ns"] is not None:
            entries.append(event("RELEASE", "main", sample["release_ns"], event_id))
        for point in observed:
            entries.append(event("DEVICE_DETECTED", "main", point["timestamp_ns"], event_id))
        for stage, role in (("SERVER_RECEIVED", "spring"), ("DECIDED", "spring"), ("COMMAND_RECEIVED", "light")):
            entries.append(event(stage, role, None, event_id, status="unobservable"))
        for point in sample["transitions"]:
            entries.append(event("APPLIED", "light", point["timestamp_ns"], event_id,
                                 status=sample["outcome"], expected_output=sample["expected_output"], observed_output=point["value"]))

    entries.extend(event("APPLIED", "light", point["timestamp_ns"], status="unmatched", observed_output=point["value"])
                   for point in unassigned)
    entries.extend(event("DEVICE_DETECTED", "main", point["timestamp_ns"], status="unmatched") for point in extra_detections)
    return entries, extra_detections


def report(output, summary):
    json_file(output / "summary.json", summary)
    status = {
        "pass": "목표 충족", "fail": "목표 미달", "insufficient_samples": "표본 부족", "inconclusive": "판정 보류",
    }
    lines = ["# 버튼→LED 기준선", "", "| SLO | 관측 | 판정 |", "| --- | --- | --- |"]
    descriptions = {"p99_1_s": "지연 p99 ≤ 1,000 ms", "on_time_99_pct": "1초 내 완료 ≥ 99%",
                    "accuracy_99_9_pct": "1초 내 정확한 단일 처리 ≥ 99.9%"}
    for key, item in summary["slo"].items():
        value = item["observed"]
        shown = "—" if value is None else f"{value:.1f} ms" if key == "p99_1_s" else f"{value:.2%}"
        line = f"| {descriptions[key]} | {shown} (n={item['samples']}) | {status[item['status']]} |"
        lines.append(line)
        print(f"{descriptions[key]} · {shown} · {status[item['status']]}", flush=True)
    values = summary["latency_ms"]
    counts = summary["counts"]
    lines.extend([
        "", f"지연 표본 {values['samples']}건 · p50 {values['p50']} ms · p95 {values['p95']} ms · p99 {values['p99']} ms",
        "", f"주입 {counts['injected']} / {counts['scheduled']}건 · 기한 내 {counts['on_time']}건 · 늦은 완료 {counts['late']}건",
        f"미출력 {counts['missing']}건 · 중복 전이 {counts['duplicate']}건 · 오출력 {counts['incorrect']}건 · 귀속 보류 {counts['unmatched']}건",
    ])
    if summary["issues"]:
        lines.extend(["", "판정에 영향을 준 관측 항목 " + ", ".join(summary["issues"])])
    if 0 < values["samples"] < 100:
        lines.extend(["", "이 실행의 p99는 표본 최댓값입니다."])
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(repo, output, prefix, count, seed):
    profile = {**PROFILE, "count": count, "seed": seed}
    checkpoint = output / "benchmark-checkpoint.json"
    with open_run(repo, output, prefix, "virtual-io-button-normal") as (lab, ready):
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update({
            "workflow": WORKFLOW, "benchmarks": ["P0-01", "P0-02"], "input_profile": profile,
            "network_impairment": "none", "clock_id": CLOCK, "runtime": runtime(lab),
            "initial_led": ready["led"], "slo_targets": SLO_TARGETS, "deadline_ms": DEADLINE_MS,
            "percentile_method": "nearest_rank",
            "analyzer_sha256": hashlib.sha256(Path(__file__).with_name("benchmark_result.py").read_bytes()).hexdigest(),
        })
        json_file(manifest_path, manifest)
        json_file(checkpoint, {"phase": "replaying", "injected": 0, "scheduled": count})
        records = replay(lab, output, profile)
        start = next((row for row in records if row["event"] == "run_start"), None)
        end = next((row for row in records if row["event"] == "run_end"), None)
        if not start or not end:
            raise RuntimeError("입력 재생의 시작 또는 종료 기록이 없습니다.")
        started, stopped = start["timestamp_ns"], end["timestamp_ns"]
        inputs = [row for row in records if row["event"] == "input"]
        releases = {row["sample"]: row for row in records if row["event"] == "release"}
        http = Counter(row["status"] for row in records if row["event"] == "http")
        interval_ns = profile["http_interval_ms"] * MS
        http_slots = (start["http_until_ns"] - started + interval_ns - 1) // interval_ns
        lab.check_processes()
        final = lab.probe()

        pins = {}
        for role, kind, pin in (("main", "gpio_read", 20), ("light", "gpio", 18)):
            pins[role] = [row for row in json_lines(lab.logs(role)) if row.get("event") == kind and
                          row["pin"] == pin and started <= row["timestamp_ns"] <= stopped]
        write_records(output / "gpio.jsonl", ({**row, "node_id": role} for role, rows in pins.items() for row in rows))
        samples, unmatched = classify(inputs, pins["light"], ready["led"], profile["window_ms"] * MS, DEADLINE_MS * MS)
        detected = [row for row in pins["main"] if row["value"] == 0]
        events, extra_detections = trace(manifest["project"], samples, releases, detected, unmatched)
        write_records(output / "events.jsonl", events)
        write_records(output / "samples.jsonl", samples)

        issues = []
        if http["ok"] != 2 * http_slots or http["error"] or http["skipped"]:
            issues.append("http_profile_incomplete")
        if unmatched or extra_detections:
            issues.append("unmatched_gpio_transitions")
        if len(releases) != len(inputs) or any(
            sample["latency_ns"] is not None and len(sample["detected_ns"]) != 1 for sample in samples
        ):
            issues.append("gpio_evidence_incomplete")
        if set(final.get("sessions", [])) != set(role_ips(prefix).values()):
            issues.append("pi_connectivity_at_end")

        summary = summarize(samples, count, issues)
        summary.update({"run_id": manifest["project"], "workflow": WORKFLOW, "http_requests": dict(http),
                        "http_requests_scheduled": http_slots * 2, "unmatched_outputs": len(unmatched)})
        report(output, summary)
    json_file(checkpoint, {"phase": "completed", "injected": len(inputs), "scheduled": count})
    print(f"결과 · {output / 'summary.md'}", flush=True)
    return summary
