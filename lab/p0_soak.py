"""Collect scheduled stability observations and resource epochs."""

from concurrent.futures import ThreadPoolExecutor
from math import ceil
from subprocess import SubprocessError
from time import monotonic, monotonic_ns, sleep

from .p0_metrics import resource_result
from .p0_recovery import button_probe, sensor_probe


ROLES = ("spring", "main", "fire", "air", "light")
PROCESSES = {"spring/java"} | {f"{role}/{component}" for role in ROLES[1:] for component in ("bridge", "native")}
SOAK_TARGET_SECONDS = 24 * 60 * 60
RESOURCE_SCRIPT = r"""
for p in /proc/[0-9]*; do
    name=$(cat "$p/comm" 2>/dev/null) || continue
    case "$name" in
        java) component=java ;;
        node) component=native ;;
        python*)
            args=$(tr '\000' ' ' < "$p/cmdline") || continue
            case "$args" in *"/bridge.py"*) component=bridge ;; *) continue ;; esac ;;
        *) continue ;;
    esac
    ticks=$(sed 's/^.*) //' "$p/stat" 2>/dev/null | awk '{print $20}')
    values=$(awk '/^VmRSS:/ {rss=$2} /^Threads:/ {threads=$2}
                  END {if (rss && threads) print rss,threads}' "$p/status" 2>/dev/null)
    fds=$(ls -1 "$p/fd" 2>/dev/null) || continue
    count=$(printf '%s\n' "$fds" | awk 'NF {n++} END {print n+0}')
    [ -n "$ticks" ] && [ -n "$values" ] || continue
    printf '%s %s %s %s %s\n' "$component" "${p##*/}" "$ticks" "$values" "$count"
done
"""


def collect_role(run, role):
    try:
        raw = run.lab.run("exec", "-T", role, "sh", "-c", RESOURCE_SCRIPT)
        result = {}
        for line in raw.splitlines():
            component, pid, ticks, rss, threads, fds = line.split()
            name = f"{role}/{component}"
            if name in result:
                raise ValueError(f"duplicate process {name}")
            result[name] = {"pid": int(pid), "start_ticks": int(ticks),
                            "rss_kib": int(rss), "threads": int(threads), "fds": int(fds)}
        return result, None, False
    except (OSError, ValueError, RuntimeError, SubprocessError) as error:
        try:
            stopped = not run.state(role)["State"]["Running"]
        except (OSError, ValueError, RuntimeError, SubprocessError):
            stopped = False
        return {}, str(error), stopped


def stimulus_loop(run, origin, origin_ns, deadline):
    results = []
    phases = {"fire": 0, "air": 0, "blinds": 0}
    script = ("button", "fire", "air", "blinds")
    bounds = {"button": 5, "fire": 30, "air": 10, "blinds": 10}
    planned = origin
    while planned < deadline:
        kind = script[len(results) % len(script)]
        if planned + bounds[kind] + 1 > deadline:
            break
        name = f"soak-{kind}-{len(results) + 1}"
        sleep(max(0, planned - monotonic()))
        actual = monotonic_ns()
        missed = monotonic() >= planned + 1
        if missed:
            completed = False
        elif kind == "button":
            completed = button_probe(run, name, bounds[kind])
        else:
            completed = sensor_probe(run, kind, name, bounds[kind], phases[kind])
            phases[kind] = (phases[kind] + 1) % (4 if kind == "blinds" else 2)
        row = {"probe": name, "kind": kind, "completed": completed, "missed": missed,
               "scheduled_ns": origin_ns + int((planned - origin) * 1e9), "started_ns": actual}
        run.record("soak-probes.jsonl", row)
        results.append(row)
        planned += bounds[kind] + 1
    return results


def collect(run, duration, interval, warmup):
    origin = monotonic()
    origin_ns = monotonic_ns()
    end = origin + duration
    scheduled = ceil(duration / interval)
    rows = []
    with ThreadPoolExecutor(max_workers=7) as pool:
        probes = pool.submit(stimulus_loop, run, origin, origin_ns, end)
        for slot in range(1, scheduled + 1):
            planned = origin + (slot - 1) * interval
            sleep(max(0, planned - monotonic()))
            if monotonic() >= planned + interval or monotonic() >= end:
                run.record("observations.jsonl", {"slot": slot, "status": "missed"})
                continue
            futures = {role: pool.submit(collect_role, run, role) for role in ROLES}
            status = pool.submit(run.http)
            processes = {}
            errors = {}
            stopped = []
            for role, future in futures.items():
                resources, error, exited = future.result()
                processes.update(resources)
                if error:
                    errors[role] = error
                if exited:
                    stopped.append(role)
            body = status.result()
            row = {"slot": slot, "scheduled_ns": origin_ns + (slot - 1) * interval * 1_000_000_000,
                   "timestamp_ns": monotonic_ns(), "processes": processes, "errors": errors, "stopped_roles": stopped,
                   "connected": len(body["sessions"]) if "sessions" in body else None,
                   "http_ok": "data" in body, "sensors": body.get("data"),
                   "http_error": body.get("probe_error")}
            run.record("resources.jsonl", row)
            rows.append(row)
            run.checkpoint("soak", scheduled=scheduled, slot=slot, samples=len(rows))
            width = 20 * slot // scheduled
            print(f"[{'#' * width}{'-' * (20 - width)}] 안정성 관측 {slot}/{scheduled}", flush=True)
        sleep(max(0, end - monotonic()))
        functions = probes.result()
    summary = resource_result(rows, scheduled, PROCESSES, ceil(warmup / interval))
    missing = summary["missing_samples"] or summary["missing_process_observations"]
    errors = sum(bool(row["errors"]) or not row["http_ok"] for row in rows)
    exits = sum(bool(row["stopped_roles"]) or any(
        name not in row["processes"] and name.split("/")[0] not in row["errors"]
        for name in PROCESSES
    ) for row in rows)
    failed = sum(not row["completed"] for row in functions)
    status = ("fail" if summary["process_restarts"] or exits else "inconclusive" if missing or errors else
              "insufficient_duration" if duration < SOAK_TARGET_SECONDS else "pass")
    summary.update(duration_seconds=duration, interval_seconds=interval, warmup_seconds=warmup,
                   observation_errors=errors, samples_with_confirmed_process_absence=exits,
                   functional_probe_count=len(functions), functional_probe_failures=failed,
                   functional_probes=functions, slo={"duration_seconds": SOAK_TARGET_SECONDS,
                   "max_unplanned_restarts": 0, "status": status})
    return summary
