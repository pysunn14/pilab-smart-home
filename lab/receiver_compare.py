"""Compare both button paths at the Spring receive boundary."""

import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import Counter
from pathlib import Path

from .benchmark import MS, replay, write_records
from .p0_metrics import percentiles
from .receiver_benchmark import analyze, verify_clock
from .receiver_preview import LEGACY_PROFILE, PROFILE, legacy_receipts, main_events, spring_receipts
from .smoke import json_file, open_run


DEFAULT_COUNT = 12
WINDOW_NS = 4_000 * MS
INPUT_PROFILE = {"slot_ms": 5_000, "pulse_ms": 250, "release_ms": 100, "lead_ms": 500, "drain_ms": 0}


def legacy_samples(inputs, release_times, readings, receipts, window_ns):
    if not inputs:
        return []
    first = inputs[0]["timestamp_ns"]
    baseline = [row for row in receipts if row["timestamp_ns"] < first]
    if not baseline:
        raise ValueError("레거시 버튼의 초기 수신 상태가 없습니다.")

    value = baseline[-1]["value"]
    changes = []
    for row in receipts:
        if row["timestamp_ns"] >= first and row["value"] != value:
            changes.append(row["timestamp_ns"])
            value = row["value"]
    falls = [row["timestamp_ns"] for row in readings]

    samples = []
    aligned = True
    previous_window_end = first - 1
    previous_release = first - 1
    for row in inputs:
        start = row["timestamp_ns"]
        release = release_times[row["sample"]]
        end = start + window_ns
        start_change = bisect_left(changes, start)
        start_fall = bisect_left(falls, start)
        received = changes[start_change:bisect_right(changes, end)]
        detected = falls[start_fall:bisect_right(falls, release)]
        # A late change or an unpaired poll leaves no reliable identity for later toggles.
        aligned = (aligned and start > previous_window_end
                   and start_change == bisect_right(changes, previous_window_end)
                   and start_fall == bisect_right(falls, previous_release))
        if not aligned:
            outcome = "ambiguous"
        elif not received and len(detected) <= 1:
            outcome = "missing"
            aligned = not detected
        elif len(detected) == len(received) == 1 and detected[0] <= received[0]:
            outcome = "received"
        else:
            outcome = "ambiguous"
            aligned = False
        samples.append({**row, "detected_ns": detected, "received_ns": received, "outcome": outcome,
                        "latency_ns": received[0] - start if outcome == "received" else None})
        previous_window_end = end
        previous_release = release
    return samples


def measure(repo, output, prefix, count, *, legacy):
    profile = {**INPUT_PROFILE, "count": count + 1}
    node = "node.py" if legacy else "receiver_node.py"
    action = "receiver-benchmark-button" if legacy else "benchmark-button"
    session_profile = LEGACY_PROFILE if legacy else PROFILE
    checkpoint = output / "benchmark-checkpoint.json"
    with open_run(repo, output, prefix, session_profile) as (lab, _):
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            workflow="gpio-to-spring-comparison", input_profile=profile, warmup_inputs=1,
            per_input_window_ns=WINDOW_NS, transport="wss" if legacy else "ws", pi_count=4 if legacy else 1,
            clock_id="lab-linux-monotonic", percentile_method="nearest_rank",
            analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        )
        manifest["clock_before"] = verify_clock(lab, node_script=node)
        json_file(manifest_path, manifest)
        json_file(checkpoint, {"phase": "replaying", "injected": 0, "scheduled": profile["count"]})
        workload = replay(lab, output, profile, node_script=node, action=action)
        if not workload or workload[-1]["event"] != "run_end":
            raise RuntimeError("버튼 입력의 관측 종료 시각이 없습니다.")
        inputs = [row for row in workload if row["event"] == "input"]
        releases = {row["sample"]: row["timestamp_ns"] for row in workload if row["event"] == "release"}
        end_ns = workload[-1]["timestamp_ns"]
        if inputs and inputs[-1]["timestamp_ns"] + WINDOW_NS > end_ns:
            raise RuntimeError("마지막 버튼의 수신 관측 창이 완료되지 않았습니다.")

        events = main_events(lab)
        if legacy:
            presses = [row for row in events if row["event"] == "gpio_read" and row["pin"] == 20 and row["value"] == 0]
            receipts = legacy_receipts(lab)
        else:
            presses = [row for row in events if row["event"] == "button_pressed"]
            receipts = spring_receipts(lab)
        write_records(output / "mainpi-presses.jsonl", presses)
        write_records(output / "spring-receipts.jsonl", receipts)
        lab.check_processes()
        manifest["clock_after"] = verify_clock(lab, node_script=node)
        json_file(manifest_path, manifest)
        if manifest["clock_before"]["sources"] != manifest["clock_after"]["sources"]:
            raise RuntimeError("실행 도중 Linux 단조 시계 기준이 바뀌었습니다.")

        if legacy:
            samples = legacy_samples(inputs, releases, presses, receipts, WINDOW_NS)
            diagnostics = {}
        else:
            summary, samples = analyze(inputs, presses, receipts, end_ns, window_ns=WINDOW_NS)
            diagnostics = {key: summary["counts"][key] for key in ("unmatched_presses", "unmatched_receipts")}
        write_records(output / "samples.jsonl", samples)
        if not samples or samples[0]["sample"] != 1 or samples[0]["outcome"] != "received":
            raise RuntimeError("준비 입력의 Spring 수신을 확인할 수 없습니다.")
        measured = samples[1:]
        counts = Counter(received=0, missing=0, duplicate=0, ambiguous=0, invalid_time=0)
        counts.update(sample["outcome"] for sample in measured)
        counts.update(scheduled=count, injected=len(measured), not_injected=count - len(measured))
        summary = {"counts": dict(counts), "diagnostics": diagnostics,
                   "latency_ms": percentiles([sample["latency_ns"] / MS for sample in measured
                                             if sample["latency_ns"] is not None])}
        json_file(output / "summary.json", summary)
    json_file(checkpoint, {"phase": "completed", "injected": len(inputs), "scheduled": profile["count"]})
    return summary


def run(repo, output, prefix, count):
    output.mkdir(parents=True)
    report = {}
    for label, legacy in (("legacy", True), ("refactored", False)):
        print(f"Spring 수신 구간 · {label}", flush=True)
        report[label] = measure(repo, output / label, prefix, count, legacy=legacy)
    json_file(output / "comparison.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"결과 · {output / 'comparison.json'}", flush=True)
