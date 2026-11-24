"""Compare the new virtual button-to-light path with and without queue persistence."""

import json
from collections import Counter, defaultdict

from .benchmark import replay, runtime, write_records
from .benchmark_result import DEADLINE_MS, MS, summarize
from .light_ack_delay import COMMAND, observed
from .light_ack_retry import spring_probe
from .receiver_benchmark import DEFAULT_COUNT, INPUT_PROFILE
from .receiver_preview import LIGHT_BENCHMARK_PROFILE, main_events, parse_button_receipts
from .smoke import json_file, json_lines, open_run


def analyze(inputs, presses, receipts, gpio, scheduled, issues=()):
    if len({row["timestamp_ns"] for row in inputs}) != len(inputs):
        raise ValueError("GPIO 입력 시각이 중복되어 이벤트를 구별할 수 없습니다.")
    if len({row["seq"] for row in presses}) != len(presses):
        raise ValueError("MainPi 송신 순번이 재사용되었습니다.")

    by_edge = defaultdict(list)
    by_seq = defaultdict(list)
    by_command = defaultdict(list)
    for press in presses:
        by_edge[press["edge_ns"]].append(press)
    for receipt in receipts:
        by_seq[receipt["seq"]].append(receipt)
    for output in gpio:
        by_command[output["commandId"]].append(output)

    samples = []
    observed_order = []
    matched_commands = set()
    matched_edges = {row["timestamp_ns"] for row in inputs}
    matched_receipts = 0
    expected = False
    for row in inputs:
        edge = row["timestamp_ns"]
        matching_presses = by_edge[edge]
        matching_receipts = [receipt for press in matching_presses for receipt in by_seq[press["seq"]]]
        command_ids = list(dict.fromkeys(receipt["commandId"] for receipt in matching_receipts))
        outputs = [point for command_id in command_ids for point in by_command[command_id]]
        matched_commands.update(command_ids)
        matched_receipts += len(matching_receipts)
        observed_order.extend((point["timestamp_ns"], row["sample"]) for point in outputs)

        expected = not expected
        latency = None
        if len(matching_presses) > 1 or len(matching_receipts) > 1 or len(outputs) > 1:
            outcome = "duplicate"
        elif not outputs:
            outcome = "missing"
        elif outputs[0]["timestamp_ns"] < edge:
            outcome = "unmatched"
        elif outputs[0]["on"] != expected:
            outcome = "incorrect"
        else:
            latency = outputs[0]["timestamp_ns"] - edge
            outcome = "on_time" if latency <= DEADLINE_MS * MS else "late"
        samples.append({"input": row, "command_ids": command_ids, "expected_output": expected,
                        "transitions": outputs, "outcome": outcome, "latency_ns": latency})

    issues = list(issues)
    if any(press["edge_ns"] not in matched_edges for press in presses):
        issues.append("unmatched_mainpi_presses")
    if matched_receipts != len(receipts):
        issues.append("unmatched_spring_receipts")
    if any(point["commandId"] not in matched_commands for point in gpio):
        issues.append("unmatched_gpio_writes")
    applied_order = [sample for _, sample in sorted(observed_order)]
    if applied_order != sorted(applied_order):
        issues.append("out_of_order_gpio_writes")
    return summarize(samples, scheduled, issues), samples


def run_case(repo, output, prefix, count, *, persistent):
    profile = {**INPUT_PROFILE, "count": count}
    with open_run(repo, output, prefix, LIGHT_BENCHMARK_PROFILE, persist_light_queue=persistent) as (lab, _):
        lab.progress(5)
        observed(lab, lambda: lab.logs("spring"),
                 lambda log: "light_state_reported on=false result=INITIALIZED" in log,
                 "LightPi의 초기 꺼짐 상태")
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(workflow="button-led", input_profile=profile, clock_id="lab-linux-monotonic",
                        percentile_method="nearest_rank", clock_before=runtime(lab))
        json_file(manifest_path, manifest)

        checkpoint = output / "benchmark-checkpoint.json"
        json_file(checkpoint, {"phase": "replaying", "scheduled": count, "injected": 0})
        workload = replay(lab, output, profile, node_script="receiver_node.py")
        if not workload or workload[-1]["event"] != "run_end":
            raise RuntimeError("버튼 입력의 관측 종료 시각이 없습니다.")

        end_ns = workload[-1]["timestamp_ns"]
        inputs = [row for row in workload if row["event"] == "input"]
        presses = [row for row in main_events(lab) if row["event"] == "button_pressed"]
        spring = lab.logs("spring")
        receipts = parse_button_receipts(spring)
        light = json_lines(lab.logs("light"))
        gpio = [row for row in light if row["event"] == "gpio" and row["offset"] == 18]
        write_records(output / "mainpi-presses.jsonl", presses)
        write_records(output / "spring-receipts.jsonl", receipts)
        write_records(output / "light-gpio.jsonl", gpio)

        lab.check_processes()
        manifest["clock_after"] = runtime(lab)
        json_file(manifest_path, manifest)
        if manifest["clock_before"]["clock_sources"] != manifest["clock_after"]["clock_sources"]:
            raise RuntimeError("실행 도중 Linux 단조 시계 기준이 바뀌었습니다.")

        pending = spring_probe(lab, "light-pending")
        confirmed = [command for event, command, _, result in COMMAND.findall(spring)
                     if event == "applied" and result == "CONFIRMED"]
        issues = []
        if any(row["event"] == "connection_lost" for row in light):
            issues.append("lightpi_disconnected")
        if any(event["timestamp_ns"] > end_ns for event in gpio):
            issues.append("gpio_writes_after_observation")
        observed_gpio = [event for event in gpio if event["timestamp_ns"] <= end_ns]
        if Counter(confirmed) != Counter(event["commandId"] for event in gpio) or pending:
            issues.append("incomplete_light_acknowledgment")
        report, samples = analyze(inputs, presses, receipts, observed_gpio, count, issues)
        report.update(observation_end_ns=end_ns, confirmed_commands=len(confirmed), pending_commands=pending)
        write_records(output / "samples.jsonl", samples)
        json_file(output / "summary.json", report)
    json_file(checkpoint, {"phase": "completed", "scheduled": count, "injected": len(inputs)})
    lab.progress(5, "completed")
    return report


def run(repo, output, prefix, count):
    output.mkdir(parents=True)
    checkpoint = output / "comparison-checkpoint.json"
    reports = {}
    for mode, persistent in (("memory", False), ("file", True)):
        print(f"큐 저장 방식 · {mode}", flush=True)
        json_file(checkpoint, {"phase": mode, "completed": list(reports)})
        reports[mode] = run_case(repo, output / mode, prefix, count, persistent=persistent)
    json_file(output / "summary.json", reports)
    json_file(checkpoint, {"phase": "completed", "completed": list(reports)})
    print(f"결과 · {output / 'summary.json'}", flush=True)
