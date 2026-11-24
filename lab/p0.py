"""Orchestrate the P0 baseline with independent Compose trials."""

import hashlib
import json
from pathlib import Path

from . import benchmark
from .p0_controls import CASES, burst, sensors
from .p0_recovery import FAULTS, summarize_recovery, trial
from .p0_soak import collect
from .p0_support import Run
from .smoke import json_file, open_run


SCENARIOS = ("all", "burst", "fire", "air", "blinds", "recovery", "network", "tcp", "spring", "soak")


def execute(repo, output, prefix, profile, settings, experiment):
    with open_run(repo, output, prefix, profile) as (lab, ready):
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        run = Run(lab, output, manifest)
        clocks = {role: run.node(role, "p0-clock") for role in ("main", "fire", "air", "light")}
        references = {(record["boot_id"], tuple(record["monotonic_offset"])) for record in clocks.values()}
        if len(references) != 1:
            raise RuntimeError("Pi 컨테이너 간 단조 시계 기준이 일치하지 않습니다.")
        manifest.update(workload=settings, network_impairment="none_before_scenario", clock_sources=clocks,
                        clock_ids=["host-monotonic", "lab-linux-monotonic"],
                        analysis_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in sorted(Path(__file__).parent.glob("p0*.py"))})
        json_file(manifest_path, manifest)
        run.checkpoint("running")
        with run.normal_load("normal"):
            summary = experiment(run, ready)
        http_rows = (output / "normal-http.jsonl").read_text().splitlines()
        http = {}
        for line in http_rows:
            key = json.loads(line)["status"]
            http[key] = http.get(key, 0) + 1
        summary["http_probe_counts"] = http
        json_file(output / "summary.json", summary)
    run.checkpoint("completed")
    return summary


def report(output, summary):
    def ms(value):
        return "—" if value is None else f"{value:,.1f} ms"

    lines = ["# PiLab P0 기준선", "", "| 시나리오 | 관측 |", "| --- | --- |"]
    for name, result in summary.items():
        if name == "button_normal":
            lines.append(f"| 버튼→LED | {result['counts']['on_time']}/{result['counts']['injected']} 기한 내 · "
                         f"p50 {ms(result['latency_ms']['p50'])} · p99 {ms(result['latency_ms']['p99'])} |")
        elif name == "burst":
            seq = result["sequence"]
            lines.append(f"| 버튼 밀집 | C 감지 {seq['detected']}/{seq['scheduled']} · "
                         f"LED 전이 {len(seq['observed'])}/{len(seq['expected'])} |")
        elif name == "actuators":
            for path, metrics in result.items():
                if path == "http_probe_counts":
                    continue
                latencies = metrics["latency_ms"]
                lines.append(f"| {path} | 성공 시나리오 {metrics['counts'].get('completed', 0)}/{metrics['eligible']} · "
                             f"p50 {ms(latencies['p50'])} · p99 {ms(latencies['p99'])} (n={latencies['samples']}) |")
        elif name.startswith("recovery_"):
            lines.append(f"| {name} | 기능 복구 {result['recovered']}/{result['valid']} · "
                         f"복구 p95 {ms(result['recovered_ms']['p95'])} · SLO {result['slo']['status']} |")
        else:
            lines.append(f"| 안정성 | {result['duration_seconds']}초 · 프로세스 관측 {result['live_samples']}/{result['scheduled_samples']} · "
                         f"재시작 {result['process_restarts']}건 · 기능 확인 실패 {result['functional_probe_failures']}건 · "
                         f"SLO {result['slo']['status']} |")
    (output / "p0-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(repo, output, prefix, selection, repeats, samples, seed, duration, interval, warmup):
    if selection in ("all", "recovery"):
        output.mkdir(parents=True)
    summary = {}
    if selection == "all":
        summary["button_normal"] = benchmark.run(repo, output / "button-normal", prefix, samples, seed)

    if selection == "burst" or selection == "all":
        path = output if selection == "burst" else output / "burst"
        summary["burst"] = execute(repo, path, prefix, "p0-button-burst",
                                   {"pairs": repeats, "interval_ms": 360, "pulse_ms": 160, "pair_slot_ms": 5000},
                                   lambda lab, ready: {"sequence": burst(lab, repeats, ready["led"])})
    selected = list(CASES) if selection == "all" else [selection] if selection in CASES else []
    if selected:
        path = output / "actuators" if selection == "all" else output
        summary["actuators"] = execute(repo, path, prefix, "p0-actuators",
                                       {"repeats": repeats, "stimuli": {key: CASES[key] for key in selected}},
                                       lambda lab, ready: sensors(lab, repeats, selected))

    faults = FAULTS if selection in ("all", "recovery") else (selection,) if selection in FAULTS else ()
    for fault in faults:
        trials = []
        for index in range(1, repeats + 1):
            path = output / f"{fault}-{index}" if selection in ("all", "recovery") else (
                output if repeats == 1 else output / str(index))
            if repeats > 1 and selection in FAULTS and index == 1:
                output.mkdir(parents=True)
            measured = execute(repo, path, prefix, f"p0-recovery-{fault}",
                               {"fault": fault, "hold_seconds": 8 if fault != "tcp" else 0,
                                "session_window_seconds": 35, "functional_window_seconds": 28},
                               lambda lab, ready: {"trial": trial(lab, fault)})
            trials.append(measured["trial"])
        summary[f"recovery_{fault}"] = summarize_recovery(fault, trials)
    if selection in ("all", "soak"):
        path = output / "soak" if selection == "all" else output
        summary["soak"] = execute(repo, path, prefix, "p0-soak",
                                  {"duration_seconds": duration, "interval_seconds": interval, "warmup_seconds": warmup},
                                  lambda lab, ready: collect(lab, duration, interval, warmup))

    json_file(output / "p0-summary.json", summary)
    report(output, summary)
    print(f"P0 결과 · {output / 'p0-summary.json'}", flush=True)
    return summary
