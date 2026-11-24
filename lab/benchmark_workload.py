"""Schedule real GPIO writes and the normal HTTP load inside the lab clock."""

import json
import random
import ssl
from threading import Event, Lock, Thread
from time import monotonic_ns, sleep
from urllib.request import urlopen


MS = 1_000_000
output_lock = Lock()


def emit(record):
    with output_lock:
        print(json.dumps(record), flush=True)


def schedule(count, seed, slot_ms, jitter_ms):
    rng = random.Random(seed)
    return [index * slot_ms + rng.randrange(jitter_ms + 1) for index in range(count)]


def pause_until(timestamp_ns):
    delay = timestamp_ns - monotonic_ns()
    if delay > 0:
        sleep(delay / (1000 * MS))


def clock_probe(url, context=None):
    before = monotonic_ns()
    with urlopen(url, context=context, timeout=3) as response:
        spring = json.load(response)
    emit({"main_before_ns": before, "spring_ns": spring, "main_after_ns": monotonic_ns()})


def fixed_pulse_replay(profile, set_button):
    release = monotonic_ns()
    origin = release + profile["lead_ms"] * MS
    interval = profile["slot_ms"] * MS
    pulse = profile["pulse_ms"] * MS
    stable = profile["release_ms"] * MS
    emit({"event": "run_start", "timestamp_ns": origin})
    for index in range(profile["count"]):
        planned = origin + index * interval
        pause_until(max(planned, release + stable))
        # A delayed generator must leave time for the pulse and its stable release.
        if monotonic_ns() + pulse + stable >= planned + interval:
            emit({"event": "skipped", "sample": index + 1, "scheduled_ns": planned})
            continue
        actual = set_button(0)
        emit({"event": "input", "sample": index + 1, "scheduled_ns": planned, "timestamp_ns": actual})
        pause_until(actual + pulse)
        release = set_button(1)
        emit({"event": "release", "sample": index + 1, "timestamp_ns": release})
    pause_until(max(release, origin + profile["count"] * interval) + profile["drain_ms"] * MS)
    emit({"event": "run_end", "timestamp_ns": monotonic_ns()})


def http_load(server, origin, limit, interval_ns, done):
    context = ssl._create_unverified_context()  # The lab Spring instance has a generated TLS certificate.
    planned = origin
    while limit is None or planned < limit:
        wait = max(0, planned - monotonic_ns()) / (1000 * MS)
        if done.wait(wait):
            return
        started = monotonic_ns()
        if started >= planned + interval_ns:
            emit({"event": "http", "scheduled_ns": planned, "status": "skipped"})
        else:
            for resource in ("data", "control"):
                path = f"/home/{resource}"
                issue = None
                try:
                    with urlopen(f"https://{server}:8080{path}", context=context, timeout=2) as response:
                        payload = json.load(response)
                        if not isinstance(payload, dict):
                            raise ValueError("expected a JSON object")
                except (OSError, ValueError) as error:
                    issue = str(error)
                emit({"event": "http", "path": path, "scheduled_ns": planned,
                      "timestamp_ns": monotonic_ns(), "status": "error" if issue else "ok", "error": issue})
        planned += interval_ns


def replay(profile, set_button, server):
    offsets = schedule(profile["count"], profile["seed"], profile["slot_ms"], profile["jitter_ms"])
    observation_ns = profile["window_ms"] * MS
    guard_ns = (profile["slot_ms"] - profile["window_ms"]) * MS
    origin = monotonic_ns() + profile["lead_ms"] * MS
    limit = origin + offsets[-1] * MS + observation_ns + guard_ns
    emit({"event": "run_start", "timestamp_ns": origin, "http_until_ns": limit})

    done = Event()
    load = Thread(target=http_load, args=(server, origin, limit, profile["http_interval_ms"] * MS, done))
    load.start()
    try:
        for index, offset in enumerate(offsets, 1):
            planned = origin + offset * MS
            pause_until(planned)
            actual = set_button("0")
            emit({"event": "input", "sample": index, "scheduled_ns": planned, "timestamp_ns": actual})
            pause_until(actual + profile["pulse_ms"] * MS)
            released = set_button("1")
            emit({"event": "release", "sample": index, "timestamp_ns": released})
        pause_until(max(limit, actual + observation_ns + guard_ns))
    finally:
        done.set()
        load.join()
    emit({"event": "run_end", "timestamp_ns": monotonic_ns()})
