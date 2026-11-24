"""Probes for the isolated P0 Docker lab."""

import json
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from time import monotonic, monotonic_ns, sleep

from .smoke import json_file, json_lines, role_ips


class Run:
    def __init__(self, lab, output, manifest):
        self.lab = lab
        self.output = output
        self.run_id = manifest["project"]
        self.network = self.run_id + "_lab"
        self.ips = role_ips(lab.env["PILAB_PREFIX"])

    def record(self, path, value):
        with (self.output / path).open("a", buffering=1) as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")

    def event(self, stage, scenario, role=None, timestamp_ns=None, clock_id="host-monotonic", **values):
        self.record("events.jsonl", {
            "run_id": self.run_id, "event_id": scenario, "workflow": (scenario or "burst").split("-")[0], "node_id": role,
            "stage": stage, "timestamp_ns": monotonic_ns() if timestamp_ns is None and clock_id else timestamp_ns,
            "clock_id": clock_id, "status": "ok", "expected_output": None, "observed_output": None, **values,
        })

    def checkpoint(self, stage, **kwargs):
        json_file(self.output / "p0-checkpoint.json", {"stage": stage, **kwargs})

    def node(self, role, *args):
        return json.loads(self.lab.run("exec", "-T", role, "python", "/app/node.py", *args))

    def http(self):
        # Air stays attached during the MainPi link test and the local TCP cut.
        try:
            body = self.node("air", "p0-probe")
        except (ValueError, RuntimeError, subprocess.SubprocessError) as error:
            body = {"probe_error": str(error)}
        return body

    def records(self, role, since):
        return json_lines(self.lab.run("logs", "--no-log-prefix", "--since", since, role))

    def docker(self, *args):
        return subprocess.check_output([self.lab.command[0], *args], text=True, stderr=subprocess.STDOUT,
                                       env=self.lab.env, timeout=15)

    def container(self, role):
        container = self.lab.run("ps", "-a", "-q", role).strip()
        if not container or "\n" in container:
            raise RuntimeError(f"expected one container for {role}")
        return container

    def state(self, role):
        value, = json.loads(self.docker("inspect", self.container(role)))
        if value["Config"]["Labels"]["com.docker.compose.project"] != self.run_id:
            raise RuntimeError("container is outside this lab")
        return value

    @contextmanager
    def normal_load(self, name):
        load = self.output / f"{name}-http.jsonl"
        with load.open("w") as out, (self.output / f"{name}-http.stderr.log").open("w") as errors, \
             subprocess.Popen([*self.lab.command, "exec", "-T", "air", "python", "/app/node.py", "p0-http"],
                              env=self.lab.env, stdin=subprocess.PIPE, stdout=out, stderr=errors, text=True) as process:
            try:
                yield
            finally:
                if process.poll() is None:
                    process.stdin.close()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if process.returncode:
                    self.event("HTTP_OBSERVER_ERROR", name, status="error", returncode=process.returncode)


def mark_logs():
    # Allow the Docker VM wall clock a margin. GPIO timestamps are filtered again
    # using the verified monotonic clock before attributing a transition.
    return (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()


def wait_for(observe, accept, seconds, interval=0.25):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        value = observe()
        if accept(value):
            return value
        sleep(min(interval, max(0, deadline - monotonic())))
    return None
