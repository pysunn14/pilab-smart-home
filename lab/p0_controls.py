"""Sensor and dense-button scenarios on the original C and Spring paths."""

from time import monotonic, sleep

from .p0_metrics import actuator_summary, burst_result, isolated_result
from .p0_support import mark_logs
from .smoke import json_lines, led_transitions


# Each row fixes the stimulus, the fresh server value and the hardware boundary.
# Fire permits two 10 s sensor cycles plus downstream polling. Other windows
# include the original actuator cadence and, for blinds, the motor's sleep.
CASES = {
    "fire": ("fire", "gas", 30, (
        (960, 469, {"alarm": 1, "servo": 2500}), (64, 40, {"alarm": 0, "servo": 500}),
    )),
    "air": ("air", "dust", 10, (
        (900, 149.83, {"servo": 500}), (67, 11, {"servo": 1500}),
    )),
    "blinds": ("light", "light", 10, (
        (100, 100, {"pwm": 1}), (350, 350, {}), (900, 900, {"pwm": -1}), (350, 350, {}),
    )),
}

CHANNELS = {
    "fire": {("gpio", 22): "alarm", ("servo", 27): "servo"},
    "air": {("servo", 17): "servo"},
    "light": {("pwm_start", 26): "pwm"},
}


def outputs(role, records, initial, start, end):
    retained = initial.copy()
    transitions = []
    for row in sorted(records, key=lambda item: item["timestamp_ns"]):
        channel = CHANNELS[role].get((row["event"], row["pin"]))
        if channel is None or row["timestamp_ns"] < start or row["timestamp_ns"] > end:
            continue
        value = row["value"]
        if channel != "pwm" and retained.get(channel) == value:
            continue
        retained[channel] = value
        transitions.append({**row, "channel": channel})
    return transitions, retained


def measure_stimulus(run, name, step, volts, initial, expected, seconds, stop_early=False):
    role, data_key, _, _ = CASES[name]
    adc, fresh = volts
    previous = run.http().get("data", {}).get(data_key)
    changes = {channel: value for channel, value in expected.items()
               if channel == "pwm" or initial.get(channel) != value}
    since = mark_logs()
    injected = run.node(role, "p0-adc", str(adc))
    start = injected["timestamp_ns"]
    run.event("INPUT", step, role, start, "lab-linux-monotonic", expected_output=expected, adc=adc)
    deadline = monotonic() + seconds
    server_seen = False
    detected = None
    edges = []
    last_body = {}
    while monotonic() < deadline:
        records = run.records(role, since)
        edges, _ = outputs(role, records, initial, start, start + seconds * 1_000_000_000)
        if detected is None:
            detected = next((row["timestamp_ns"] for row in records if row["event"] == "adc_read" and
                             row["pin"] == 0 and row["value"] == adc and row["timestamp_ns"] >= start), None)
        if not server_seen:
            last_body = run.http()
            server_seen = previous is not None and previous != fresh and last_body.get("data", {}).get(data_key) == fresh
            # The normal band must rearm Spring's blind state before reversing.
            if role == "light" and not expected:
                server_seen &= last_body.get("control", {}).get("blindControl") == "IDLE"
            if server_seen:
                run.event("SERVER_VALUE_OBSERVED", step, "spring", data_key=data_key, value=fresh)
        observed = isolated_result(start, changes, edges) if changes else None
        if stop_early and server_seen and (not expected or (observed and observed["outcome"] == "completed")):
            break
        sleep(min(0.4, max(0, deadline - monotonic())))

    # A final read includes transitions between the last iteration and the cutoff.
    received = run.records(role, since)
    end = start + seconds * 1_000_000_000
    edges, final = outputs(role, received, initial, start, end)
    frames = [row for row in received if row["event"] == "fire_frame" and start <= row["timestamp_ns"] <= end]
    if detected is not None:
        run.event("DEVICE_DETECTED", step, role, detected, "lab-linux-monotonic")
    for channel in ("SERVER_RECEIVED", "DECIDED"):
        run.event(channel, step, role, clock_id=None, status="unobservable")
    if role == "fire":
        for frame in frames:
            run.event("COMMAND_RECEIVED", step, role, frame["timestamp_ns"], "lab-linux-monotonic",
                      status="unrecognized" if frame["value"] == -1 else "ok", bytes_received=frame["pin"], command=frame["value"])
    else:
        run.event("COMMAND_RECEIVED", step, role, clock_id=None, status="unobservable")
    for item in edges:
        run.event("APPLIED", step, role, item["timestamp_ns"], "lab-linux-monotonic",
                  observed_output={item["channel"]: item["value"]}, expected_output=expected)
    if changes:
        result = isolated_result(start, changes, edges)
    else:
        outcome = "extra_transition" if edges else "precondition_unmet" if expected else "completed"
        result = {"outcome": outcome, "latency_ms": None}
    if result["outcome"] == "completed" and (not server_seen or detected is None):
        result.update(outcome="unconfirmed", latency_ms=None)
    row = {"step": step, "adc": adc, "previous_server_value": previous, "fresh_server_value_observed": server_seen,
           "device_detected": detected is not None, "expected": expected, "outputs": edges, **result}
    if role == "fire":
        row["unrecognized_tcp_reads"] = sum(frame["value"] == -1 for frame in frames)
    run.record("samples.jsonl", row)
    return row, final


def sensors(run, repeats, selected):
    initial = {"fire": {"alarm": 0, "servo": 500}, "air": {"servo": 1500}, "blinds": {}}
    result = {}
    for name in selected:
        _, _, seconds, sequence = CASES[name]
        current = initial[name]
        samples = []
        for repeat in range(repeats):
            for number, (adc, fresh, expected) in enumerate(sequence, 1):
                step = f"{name}-{repeat + 1}-{number}"
                run.checkpoint("actuators", scenario=step)
                print(f"PiLab 제어 입력 {step}", flush=True)
                row, current = measure_stimulus(run, name, step, (adc, fresh), current, expected, seconds)
                samples.append(row)
        result[name] = actuator_summary(samples)
    return result


def burst(run, pairs, initial):
    since = mark_logs()
    raw = json_lines(run.lab.run("exec", "-T", "main", "python", "/app/node.py", "p0-burst", str(pairs),
                                 timeout=5 * pairs + 10))
    for row in raw:
        run.record("workload.jsonl", row)
    inputs = [item for item in raw if item["event"] == "input"]
    if len(inputs) != 2 * pairs:
        raise RuntimeError("밀집 입력 기록의 길이가 예정 시퀀스와 다릅니다.")
    start, stop = inputs[0]["timestamp_ns"], raw[-1]["timestamp_ns"]
    values = [1 - initial if index % 2 == 0 else initial for index in range(len(inputs))]
    for item, value in zip(inputs, values):
        run.event("INPUT", f"burst-{item['sample']}", "main", item["timestamp_ns"], "lab-linux-monotonic",
                  expected_output=value, scheduled_ns=item["scheduled_ns"])
    pin = [row for row in run.records("light", since)
           if row["event"] == "gpio" and row["pin"] == 18 and start <= row["timestamp_ns"] <= stop]
    for item in pin:
        run.event("APPLIED", None, "light", item["timestamp_ns"], "lab-linux-monotonic",
                  observed_output=item["value"], status="unmatched")
    detected = [row for row in run.records("main", since)
                if row["event"] == "gpio_read" and row["value"] == 0 and start <= row["timestamp_ns"] <= stop]
    for item in detected:
        run.event("DEVICE_DETECTED", None, "main", item["timestamp_ns"], "lab-linux-monotonic", status="unmatched")
    return {**burst_result(values, led_transitions(pin, initial)),
            "scheduled": len(values), "detected": len(detected), "slo": {"status": "not_configured"}}
