"""Match isolated GPIO transitions and evaluate the provisional button SLOs."""

from bisect import bisect_right
from collections import Counter


MS = 1_000_000
DEADLINE_MS = 1_000
SLO_TARGETS = {
    "p99_1_s": {"limit_ms": DEADLINE_MS, "min_samples": 100},
    "on_time_99_pct": {"minimum": 0.99, "min_samples": 100},
    "accuracy_99_9_pct": {"minimum": 0.999, "min_samples": 1_000},
}
OUTCOMES = ("on_time", "late", "missing", "duplicate", "incorrect", "unmatched")


def classify(inputs, gpio, initial, window_ns, deadline_ns):
    """Use isolated windows since the legacy payload has no event ID."""
    starts = [event["timestamp_ns"] for event in inputs]
    if any(later - earlier < window_ns for earlier, later in zip(starts, starts[1:])):
        samples = [{"input": event, "outcome": "unmatched", "transitions": [], "latency_ns": None, "expected_output": None}
                   for event in inputs]
        return samples, gpio

    buckets = [[] for _ in inputs]
    unmatched = []
    changed = initial
    for point in sorted(gpio, key=lambda event: event["timestamp_ns"]):
        if point["value"] == changed:
            continue
        changed = point["value"]
        index = bisect_right(starts, point["timestamp_ns"]) - 1
        if index < 0 or point["timestamp_ns"] >= starts[index] + window_ns:
            unmatched.append(point)
        else:
            buckets[index].append(point)

    samples = []
    expected = initial
    uncertain = False
    for event, outputs in zip(inputs, buckets):
        expected = 1 - expected
        elapsed = None
        if uncertain or (unmatched and unmatched[0]["timestamp_ns"] < event["timestamp_ns"]):
            outcome = "unmatched"
            unmatched.extend(outputs)
            outputs = []
        elif not outputs:
            outcome = "missing"
        elif len(outputs) > 1:
            outcome = "duplicate"
        elif outputs[0]["value"] != expected:
            outcome = "incorrect"
        else:
            elapsed = outputs[0]["timestamp_ns"] - event["timestamp_ns"]
            outcome = "on_time" if elapsed <= deadline_ns else "late"
        # An overrun can arrive in a later window. Preserve evidence without
        # assigning a causal ID after the expected output sequence breaks.
        uncertain = elapsed is None
        samples.append({"input": event, "expected_output": expected, "transitions": outputs,
                        "outcome": outcome, "latency_ns": elapsed})
    return samples, sorted(unmatched, key=lambda event: event["timestamp_ns"])


def summarize(samples, scheduled, issues=()):
    counts = Counter(sample["outcome"] for sample in samples)
    valid = len(samples)
    latencies = sorted(sample["latency_ns"] for sample in samples if sample["latency_ns"] is not None)
    if scheduled != valid:
        issues = [*issues, "incomplete_injection"]
    if counts["unmatched"]:
        issues = [*issues, "unmatched_inputs"]

    def percentile(percent):
        rank = (percent * len(latencies) + 99) // 100
        return latencies[rank - 1] / MS if rank else None

    accuracy = counts["on_time"] + counts["late"]
    rates = {key: counts[key] / valid if valid else None for key in OUTCOMES}
    rates.update({
        "accuracy": accuracy / valid if valid else None,
        "deadline_missed": (valid - counts["on_time"]) / valid if valid else None,
    })
    p99 = percentile(99)
    comparison = {
        "p99_1_s": (p99, p99 is not None and p99 <= DEADLINE_MS, len(latencies)),
        "on_time_99_pct": (rates["on_time"], valid > 0 and counts["on_time"] * 100 >= 99 * valid, valid),
        "accuracy_99_9_pct": (rates["on_time"], valid > 0 and counts["on_time"] * 1_000 >= 999 * valid, valid),
    }
    slo = {}
    for key, target in SLO_TARGETS.items():
        value, meets, n = comparison[key]
        if issues or value is None:
            state = "inconclusive"
        elif not meets:
            state = "fail"
        elif n < target["min_samples"]:
            state = "insufficient_samples"
        else:
            state = "pass"
        slo[key] = {**target, "observed": value, "samples": n, "status": state}

    return {
        "counts": {"scheduled": scheduled, "injected": valid, "not_injected": scheduled - valid,
                   **{key: counts[key] for key in OUTCOMES}},
        "latency_ms": {"samples": len(latencies), "p50": percentile(50), "p95": percentile(95), "p99": p99},
        "rates": rates,
        "issues": list(issues),
        "slo": slo,
    }
