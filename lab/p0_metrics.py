"""Pure aggregations shared by the additional P0 scenarios."""

from collections import Counter, defaultdict
from math import ceil
from statistics import median


def percentiles(values):
    ordered = sorted(value for value in values if value is not None)
    return {"samples": len(ordered), **{
        f"p{p}": ordered[ceil(p * len(ordered) / 100) - 1] if ordered else None
        for p in (50, 95, 99)
    }}


def burst_result(expected, observed):
    return {
        "expected": expected, "observed": observed, "sequence_matches": expected == observed,
        "output_shortfall": max(0, len(expected) - len(observed)),
        "additional_outputs": max(0, len(observed) - len(expected)),
        "first_differing_index": next((i for i, (a, b) in enumerate(zip(expected, observed)) if a != b),
                                      min(len(expected), len(observed))) if expected != observed else None,
        "per_input_latency_ms": None, "per_input_loss_rate": None,
    }


def isolated_result(input_ns, expected, points):
    found = {}
    extra = 0
    incorrect = 0
    for point in points:
        channel = point["channel"]
        if channel not in expected or point["timestamp_ns"] < input_ns:
            continue
        if channel in found:
            extra += 1
        elif point["value"] == expected[channel]:
            found[channel] = point["timestamp_ns"]
        else:
            incorrect += 1
    outcome = ("extra_transition" if extra else "incorrect" if incorrect else
               "completed" if found.keys() == expected.keys() else "missing")
    return {"outcome": outcome, "extra_transitions": extra, "incorrect_outputs": incorrect,
            "observed": found, "latency_ms": (max(found.values()) - input_ns) / 1e6 if outcome == "completed" else None}


def recovery_result(rows, limit_ms=None):
    valid = [row for row in rows if row["valid"]]
    timings = {key: percentiles(row.get(key) for row in valid)
               for key in ("detected_ms", "reconnected_ms", "recovered_ms")}
    count = len(valid)
    recovered = timings["recovered_ms"]["samples"]
    within = sum(row.get("recovered_ms") is not None and row["recovered_ms"] <= limit_ms
                 for row in valid) if limit_ms is not None else None
    status = "not_configured"
    if limit_ms is not None:
        p95 = timings["recovered_ms"]["p95"]
        status = ("inconclusive" if count != len(rows) or not count else
                  "fail" if p95 is None or p95 > limit_ms else "inconclusive" if recovered < count else
                  "insufficient_samples" if count < 20 else "pass")
    return {**timings, "scheduled": len(rows), "valid": count, "recovered": recovered,
            "not_detected": count - timings["detected_ms"]["samples"], "not_recovered": count - recovered,
            "recovery_success_rate": recovered / count if count else None,
            "within_target_rate": within / count if count and within is not None else None,
            "slo": {"p95_limit_ms": limit_ms, "status": status}}


def resource_result(rows, scheduled, expected_roles, warmup_slots):
    epochs = defaultdict(list)
    live = connected = missing_processes = 0
    restarts = 0
    for row in rows:
        current = row["processes"]
        alive = expected_roles.issubset(current)
        live += alive
        missing_processes += len(expected_roles - current.keys())
        connected += row.get("connected") or 0
        for role, state in current.items():
            parts = epochs[role]
            key = (state["pid"], state["start_ticks"])
            if not parts or key != parts[-1]["identity"]:
                restarts += bool(parts)
                parts.append({"identity": key, "first_slot": row["slot"], "rows": []})
            parts[-1]["rows"].append((row["slot"], state))

    for parts in epochs.values():
        for epoch in parts:
            observed = epoch.pop("rows")
            stable = [(slot, row) for slot, row in observed if slot > warmup_slots]
            epoch.update({"samples": len(observed), "stable_samples": len(stable), "last_slot": observed[-1][0]})
            width = max(1, len(stable) // 4)
            for key in ("rss_kib", "fds", "threads"):
                values = [row[key] for _, row in stable]
                delta = median(values[-width:]) - median(values[:width]) if len(values) >= 2 else None
                slots = [slot for slot, _ in stable]
                spread = median(slots[-width:]) - median(slots[:width]) if delta is not None else None
                epoch[key] = {"max": max(values, default=None), "delta_tail_median": delta,
                              "change_per_sample_interval": delta / spread if spread else None}

    return {"scheduled_samples": scheduled, "samples": len(rows),
            "missing_samples": scheduled - len(rows), "missing_process_observations": missing_processes,
            "survival_rate": live / scheduled, "connected_rate": connected / (4 * scheduled),
            "live_samples": live, "process_restarts": restarts, "processes": dict(epochs)}


def actuator_summary(samples):
    counts = Counter(row["outcome"] for row in samples)
    latencies = percentiles(row["latency_ms"] for row in samples)
    eligible = len(samples) - counts["precondition_unmet"]
    return {"counts": dict(counts), "scheduled": len(samples), "eligible": eligible,
            "accuracy": counts["completed"] / eligible if eligible else None,
            "latency_ms": latencies, "slo": {"status": "not_configured", "deadline_ms": None}}
