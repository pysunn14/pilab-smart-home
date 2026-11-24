"""Exercise faults owned by this Compose project and prove functional recovery."""

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, monotonic_ns, sleep

from .p0_controls import CASES, measure_stimulus, outputs
from .p0_metrics import recovery_result
from .p0_support import mark_logs, wait_for
from .smoke import led_transitions


FAULTS = ("network", "tcp", "spring")
LINK_TARGET_MS = 10_000


def button_probe(run, name, seconds):
    since = mark_logs()
    original = run.http().get("control", {})
    state = original.get("lightControl")
    signal = original.get("buttonSignal")
    if state is None or signal is None or not run.state("main")["State"]["Running"]:
        return False
    live_led = run.lab.run("exec", "-T", "light", "cat", "/board/gpio/gpio18/value").strip()
    if live_led != str(int(state)):
        return False
    injected = run.node("main", "button", "0")
    timestamp = injected["timestamp_ns"]
    run.event("INPUT", name, "main", timestamp, "lab-linux-monotonic", expected_output=int(not state))
    sleep(0.25)
    run.node("main", "button", "1")

    def observe():
        records = run.records("light", since)
        edges = [row for row in records if row["event"] == "gpio" and row["pin"] == 18
                 and row["timestamp_ns"] >= timestamp]
        control = run.http().get("control", {})
        if control.get("buttonSignal") is not (not signal) or control.get("lightControl") is not (not state):
            return None
        if led_transitions(edges, int(state)) != [int(not state)]:
            return None
        return next(row for row in edges if row["value"] == int(not state))

    applied = wait_for(observe, bool, seconds)
    if applied:
        run.event("APPLIED", name, "light", applied["timestamp_ns"], "lab-linux-monotonic",
                  observed_output=applied["value"], expected_output=int(not state))
    return applied is not None


def sensor_probe(run, name, label, seconds, step=0):
    _, _, _, sequence = CASES[name]
    adc, fresh, expected = sequence[step]
    initial = {"fire": {"alarm": 1 if step else 0, "servo": 2500 if step else 500},
               "air": {"servo": 500 if step else 1500}, "blinds": {}}[name]
    row, _ = measure_stimulus(run, name, label, (adc, fresh), initial, expected, seconds, stop_early=True)
    return row["outcome"] == "completed"


def begin_fault(run, fault):
    if fault == "network":
        main = run.container("main")
        if run.network not in run.state("main")["NetworkSettings"]["Networks"]:
            return False
        run.docker("network", "disconnect", run.network, main)
        return run.network not in run.state("main")["NetworkSettings"]["Networks"]
    if fault == "spring":
        run.lab.run("stop", "-t", "2", "spring")
        return not run.state("spring")["State"]["Running"]

    since = mark_logs()
    start = run.node("main", "p0-clock")["timestamp_ns"]
    run.lab.run("exec", "-T", "main", "python", "/app/node.py", "p0-cut-tcp")
    cut = wait_for(lambda: next((row for row in run.records("main", since) if row["event"] == "tcp_shutdown"
                               and row["timestamp_ns"] >= start), None), bool, 6)
    if cut:
        run.event("TCP_SHUTDOWN_OBSERVED", fault, "main", cut["timestamp_ns"], "lab-linux-monotonic")
    return cut


def restore(run, fault):
    if fault == "network":
        run.docker("network", "connect", "--ip", run.ips["main"], run.network, run.container("main"))
        endpoint = run.state("main")["NetworkSettings"]["Networks"].get(run.network, {})
        return endpoint.get("IPAddress") == run.ips["main"]
    if fault == "spring":
        run.lab.run("start", "spring")
        return run.state("spring")["State"]["Running"]
    # The cut is one-shot. Reconnection now depends on the legacy C process.
    return True


def trial(run, fault, hold_seconds=8, reconnect_seconds=35, probe_seconds=28):
    run.checkpoint("fault", scenario=fault)
    all_ips = set(run.ips.values())
    original_ids = run.http().get("session_ids", {})
    since = mark_logs()
    before_ns = run.node("light", "p0-clock")["timestamp_ns"]
    injected = begin_fault(run, fault)
    if not injected:
        run.event("FAULT_ACTIVE", fault, status="unconfirmed", clock_id=None)
        return {"valid": False, "anomalies": ["fault_not_confirmed"]}
    active = monotonic_ns()
    run.event("FAULT_ACTIVE", fault, timestamp_ns=active)
    detected = None
    impact = set()
    during_http_failures = 0
    limit = monotonic() + (0 if fault == "tcp" else hold_seconds)
    while monotonic() < limit:
        status = run.http()
        if "sessions" not in status:
            during_http_failures += 1
        else:
            absent = {name for name, ip in run.ips.items() if ip not in status["sessions"]}
            if fault == "network":
                impact.update(absent - {"main"})
                if "main" in absent and detected is None:
                    detected = monotonic_ns()
                    run.event("DETECTED", fault, "spring", detected)
        if fault == "spring" and detected is None and "probe_error" in status:
            detected = monotonic_ns()
            run.event("DETECTED", fault, "spring", detected)
        sleep(min(0.4, max(0, limit - monotonic())))
    if fault == "tcp" and not run.state("main")["State"]["Running"]:
        detected = monotonic_ns()
        run.event("DETECTED", fault, "main", detected, evidence="container_exit_after_tcp_shutdown")
    if detected is None:
        run.event("DETECTED", fault, clock_id=None, status="unobserved")
    before_restore_ns = run.node("light", "p0-clock")["timestamp_ns"]
    led_during = [row for row in run.records("light", since) if row["event"] == "gpio" and row["pin"] == 18
                  and before_ns <= row["timestamp_ns"] <= before_restore_ns]
    for edge in led_during:
        run.event("UNEXPECTED_LED_DURING_FAULT", fault, "light", edge["timestamp_ns"], "lab-linux-monotonic",
                  observed_output=edge["value"])
    other_outputs = {}
    for role, initial in (("fire", {"alarm": 0, "servo": 500}), ("air", {"servo": 1500}), ("light", {})):
        edges, _ = outputs(role, run.records(role, since), initial, before_ns, before_restore_ns)
        other_outputs[role] = len(edges)
        for edge in edges:
            run.event("UNEXPECTED_OUTPUT_DURING_FAULT", fault, role, edge["timestamp_ns"], "lab-linux-monotonic",
                      observed_output={edge["channel"]: edge["value"]})
    restored = active if fault == "tcp" else monotonic_ns()
    if not restore(run, fault):
        return {"valid": True, "detected_ms": (detected - active) / 1e6 if detected else None,
                "anomalies": ["restore_action_unconfirmed"]}
    if fault == "network":
        restored = monotonic_ns()
    run.event("RESTORE_START", fault, timestamp_ns=restored)

    affected = all_ips if fault == "spring" else {run.ips["main"]}

    def new_sessions(status):
        ids = status.get("session_ids", {})
        return fault != "tcp" and affected.issubset(original_ids) and all(
            ip in ids and ids[ip] != original_ids[ip] for ip in affected)

    still = wait_for(run.http, lambda value: set(value.get("sessions", ())) == all_ips, reconnect_seconds)
    available = monotonic_ns() if still else None
    reconnection = available if still and new_sessions(still) else None
    if available:
        run.event("WS_AVAILABLE", fault, "spring", available, session_ids=still["session_ids"])
    probes = {}
    if available:
        if fault == "spring":
            with ThreadPoolExecutor(max_workers=4) as pool:
                tasks = {name: pool.submit(sensor_probe, run, name, f"{fault}-{name}", probe_seconds)
                         for name in CASES}
                tasks["button"] = pool.submit(button_probe, run, f"{fault}-button", probe_seconds)
                probes = {name: future.result() for name, future in tasks.items()}
        else:
            probes["button"] = button_probe(run, f"{fault}-button", probe_seconds)
    recovered = monotonic_ns() if probes and all(probes.values()) else None
    if reconnection is None and fault != "tcp" and available and new_sessions(run.http()):
        reconnection = monotonic_ns()
    if reconnection:
        run.event("RECONNECTED", fault, "spring", reconnection)
    run.event("RECOVERED", fault, timestamp_ns=recovered, clock_id="host-monotonic" if recovered else None,
              status="ok" if recovered else "unobserved")
    tcp_ms = None
    if fault == "tcp":
        link = next((row for row in run.records("main", since) if row["event"] == "tcp_connected"
                     and row["timestamp_ns"] > injected["timestamp_ns"]), None)
        if link:
            tcp_ms = (link["timestamp_ns"] - injected["timestamp_ns"]) / 1e6
            run.event("TCP_RECONNECTED", fault, "main", link["timestamp_ns"], "lab-linux-monotonic")
    return {
        "valid": True, "detected_ms": (detected - active) / 1e6 if detected else None,
        "reconnected_ms": tcp_ms if fault == "tcp" else (reconnection - restored) / 1e6 if reconnection else None,
        "recovered_ms": (recovered - restored) / 1e6 if recovered else None,
        "functional_probes": probes, "unexpected_led_writes_during_fault": len(led_during),
        "unexpected_other_outputs_during_fault": other_outputs,
        "unaffected_nodes_with_missing_sessions": sorted(impact), "http_observation_failures_during_fault": during_http_failures,
    }


def summarize_recovery(fault, trials):
    return recovery_result(trials, LINK_TARGET_MS if fault == "network" else None)
