"""Measure the virtual GPIO to Spring's typed-button receiver path."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from .benchmark import MS, replay, write_records
from .p0_metrics import percentiles
from .receiver_preview import PROFILE, main_events, spring_receipts
from .smoke import json_file, open_run


DEFAULT_COUNT = 300
INPUT_PROFILE = {"slot_ms": 200, "pulse_ms": 50, "release_ms": 10, "lead_ms": 500, "drain_ms": 2_000}


def analyze(inputs, presses, receipts, observation_end_ns, *, window_ns=None):
    input_edges = {row["timestamp_ns"] for row in inputs}
    if len(input_edges) != len(inputs):
        raise ValueError("GPIO 입력 시각이 중복되어 이벤트를 구별할 수 없습니다.")
    if len({row["seq"] for row in presses}) != len(presses):
        raise ValueError("MainPi 송신 순번이 재사용되어 수신을 귀속할 수 없습니다.")

    by_edge = defaultdict(list)
    for press in presses:
        by_edge[press["edge_ns"]].append(press["seq"])
    observed = [row for row in receipts if row["timestamp_ns"] <= observation_end_ns]
    by_seq = defaultdict(list)
    for receipt in observed:
        by_seq[receipt["seq"]].append(receipt["timestamp_ns"])

    samples = []
    counts = Counter(received=0, missing=0, duplicate=0, invalid_time=0)
    for row in inputs:
        edge = row["timestamp_ns"]
        sequences = by_edge[edge]
        limit_ns = observation_end_ns if window_ns is None else min(edge + window_ns, observation_end_ns)
        times = sorted(timestamp for seq in sequences for timestamp in by_seq[seq] if timestamp <= limit_ns)
        if not times:
            outcome = "missing"
        elif len(sequences) != 1 or len(times) != 1:
            outcome = "duplicate"
        elif times[0] < edge:
            outcome = "invalid_time"
        else:
            outcome = "received"
        counts[outcome] += 1
        samples.append({**row, "sequences": sequences, "received_ns": times, "outcome": outcome,
                        "latency_ns": times[0] - edge if outcome == "received" else None})

    matched = {row["seq"] for row in presses if row["edge_ns"] in input_edges}
    counts.update(injected=len(inputs), unmatched_presses=sum(row["edge_ns"] not in input_edges for row in presses),
                  unmatched_receipts=sum(row["seq"] not in matched for row in observed),
                  after_observation=len(receipts) - len(observed))
    latency = percentiles([row["latency_ns"] / MS for row in samples if row["latency_ns"] is not None])
    return {"counts": dict(counts), "latency_ms": latency}, samples


def verify_clock(lab, *, node_script="receiver_node.py"):
    sources = {}
    for role in ("main", "spring"):
        boot_id, *offsets = lab.run("exec", "-T", role, "cat",
                                   "/proc/sys/kernel/random/boot_id", "/proc/self/timens_offsets").splitlines()
        monotonic = next((line.split()[1:] for line in offsets if line.split()[0] == "monotonic"), [])
        if len(monotonic) != 2:
            raise RuntimeError(f"{role}의 단조 시계 오프셋을 확인할 수 없습니다.")
        sources[role] = {"boot_id": boot_id, "monotonic_offset": [int(part) for part in monotonic]}
    if sources["main"] != sources["spring"]:
        raise RuntimeError("두 컨테이너의 Linux 부팅 ID 또는 단조 시계 오프셋이 다릅니다.")

    probe = json.loads(lab.run("exec", "-T", "main", "python", f"/app/{node_script}", "clock"))
    if not probe["main_before_ns"] <= probe["spring_ns"] <= probe["main_after_ns"]:
        raise RuntimeError("Spring의 단조 시각이 MainPi의 조회 시작과 종료 사이에 없습니다.")
    return {"sources": sources, "probe": probe}


def run(repo, output, prefix, count):
    profile = {**INPUT_PROFILE, "count": count}
    checkpoint = output / "benchmark-checkpoint.json"
    with open_run(repo, output, prefix, PROFILE) as (lab, _):
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(workflow="gpio-to-spring-receiver", input_profile=profile,
                        percentile_method="nearest_rank", clock_id="lab-linux-monotonic",
                        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        manifest["clock_before"] = verify_clock(lab)
        json_file(manifest_path, manifest)
        json_file(checkpoint, {"phase": "replaying", "injected": 0, "scheduled": count})
        workload = replay(lab, output, profile, node_script="receiver_node.py")
        if not workload or workload[-1]["event"] != "run_end":
            raise RuntimeError("버튼 입력의 관측 종료 시각이 없습니다.")

        inputs = [row for row in workload if row["event"] == "input"]
        presses = [row for row in main_events(lab) if row["event"] == "button_pressed"]
        receipts = spring_receipts(lab)
        write_records(output / "mainpi-presses.jsonl", presses)
        write_records(output / "spring-receipts.jsonl", receipts)
        lab.check_processes()
        manifest["clock_after"] = verify_clock(lab)
        json_file(manifest_path, manifest)
        if manifest["clock_before"]["sources"] != manifest["clock_after"]["sources"]:
            raise RuntimeError("실행 도중 Linux 단조 시계 기준이 바뀌었습니다.")

        end_ns = workload[-1]["timestamp_ns"]
        summary, samples = analyze(inputs, presses, receipts, end_ns)
        summary["observation_end_ns"] = end_ns
        summary["counts"].update(scheduled=count, not_injected=count - len(inputs))
        write_records(output / "samples.jsonl", samples)
        json_file(output / "summary.json", summary)
    json_file(checkpoint, {"phase": "completed", "injected": len(inputs), "scheduled": count})
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"결과 · {output / 'summary.json'}", flush=True)
