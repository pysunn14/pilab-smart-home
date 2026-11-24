"""Single-owner session for interactive lab experiments."""

import json
from datetime import datetime, timezone
from queue import Empty, Queue
from subprocess import SubprocessError
from threading import Event, Lock, Thread
from uuid import uuid4

from .receiver_preview import PROFILE as RECEIVER_PROFILE
from .receiver_preview import check_button as check_receiver_button, node_command as receiver_node_command
from .smoke import CleanupError, check_button, json_file, open_run, role_ips


STAGES = {"INPUT": "main", "APPLIED": "light", "SERVER_RECEIVED": "spring"}
ACTIVE = {"starting", "running", "applying", "stopping"}


class SessionConflict(Exception):
    pass


class ExperimentSession:
    def __init__(self, repo, runs, prefix, *, spring_receiver=False):
        self.repo = repo
        self.runs = runs
        self.prefix = prefix
        self.spring_receiver = spring_receiver
        self.lock = Lock()
        self.closed = Event()
        self.commands = Queue(maxsize=1)
        self.worker = None
        self.state = {"phase": "idle", "run_id": None, "step": 0, "led": None, "connected": [],
                      "last_latency_ns": None, "events": [], "error": None, "observed_at": None}

    def snapshot(self):
        with self.lock:
            return {**self.state, "events": list(self.state["events"]),
                    "spring_receiver": self.spring_receiver}

    def request(self, action):
        with self.lock:
            phase = self.state["phase"]
            if self.closed.is_set():
                raise SessionConflict("실험 서버가 종료 중입니다.")
            if action == "start":
                if phase in ACTIVE or (self.worker is not None and self.worker.is_alive()):
                    raise SessionConflict("이미 실험이 진행 중입니다.")
                if phase == "failed" and self.state.get("cleanup_failed"):
                    raise SessionConflict("이전 실험의 정리를 확인해야 합니다.")
                run_id = "interactive-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
                self.state = {"phase": "starting", "run_id": run_id, "step": 0, "led": None, "connected": [],
                              "last_latency_ns": None, "events": [], "error": None, "observed_at": None}
                self.worker = Thread(target=self._run, args=(run_id,), name="pilab-experiment")
                self.worker.start()
            elif action in {"button", "stop"}:
                if phase != "running":
                    raise SessionConflict("실험이 준비된 뒤 조작할 수 있습니다.")
                self.state["phase"] = "applying" if action == "button" else "stopping"
                self.commands.put_nowait(action)
            else:
                raise ValueError("알 수 없는 조작입니다.")

    def close(self):
        self.closed.set()
        if self.worker is not None:
            self.worker.join()

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def _checkpoint(self, output, phase):
        json_file(output / "session.json", {"phase": phase, "observed_at": datetime.now(timezone.utc).isoformat()})

    def _observe_state(self, lab):
        lab.check_processes()
        probe = json.loads(receiver_node_command(lab, "probe")) if self.spring_receiver else lab.probe()
        if "probe_error" in probe:
            raise RuntimeError("Spring의 상태 조회가 실패했습니다. " + probe["probe_error"])
        connected = [role for role, ip in role_ips(self.prefix).items() if ip in probe["sessions"]]
        self._set(connected=connected, observed_at=datetime.now(timezone.utc).isoformat())

    def _press(self, lab, output, run_id):
        event_id = uuid4().hex
        observed = {}

        def record(stage, evidence):
            item = {"run_id": run_id, "event_id": event_id, "stage": stage,
                    "node_id": STAGES[stage], "status": "ok",
                    "workflow": RECEIVER_PROFILE if self.spring_receiver else "button-led"}
            if stage == "SERVER_RECEIVED":
                item.update(evidence)
            else:
                item.update(timestamp_ns=evidence["timestamp_ns"], clock_id="lab-linux-monotonic",
                            observed_output=evidence["value"])
            if stage == "INPUT":
                if not self.spring_receiver:
                    item["expected_output"] = 1 - self.snapshot()["led"]
                item["observed_output"] = None
                item["input_value"] = evidence["value"]
            with (output / "events.jsonl").open("a") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            observed[stage] = item
            with self.lock:
                self.state["events"] = [*self.state["events"][-63:], item]
                if stage == "APPLIED":
                    self.state["led"] = evidence["value"]

        if self.spring_receiver:
            sequence = check_receiver_button(lab, self.snapshot().get("last_sequence", 0) + 1, record)
            record("SERVER_RECEIVED", {"sequence": sequence, "observed_at": datetime.now(timezone.utc).isoformat()})
            with (output / "results.jsonl").open("a") as stream:
                stream.write(json.dumps({"event_id": event_id, "status": "received", "sequence": sequence}) + "\n")
            self._checkpoint(output, "running")
            self._set(phase="running", last_sequence=sequence)
            return

        initial = self.snapshot()["led"]
        result = check_button(lab, initial, record)
        elapsed = observed["APPLIED"]["timestamp_ns"] - observed["INPUT"]["timestamp_ns"]
        with (output / "results.jsonl").open("a") as stream:
            stream.write(json.dumps({"event_id": event_id, "status": "applied", "latency_ns": elapsed}) + "\n")
        self._checkpoint(output, "running")
        self._set(phase="running", led=result["applied"]["value"], last_latency_ns=elapsed)

    def _run(self, run_id):
        output = self.runs / run_id
        try:
            profile = RECEIVER_PROFILE if self.spring_receiver else "virtual-io-interactive"
            with open_run(self.repo, output, self.prefix, profile,
                          lambda step: self._set(step=step)) as (lab, ready):
                try:
                    connected = [role for role, ip in role_ips(self.prefix).items() if ip in ready["connected"]]
                    self._checkpoint(output, "running")
                    if not self.closed.is_set():
                        self._set(phase="running", connected=connected, led=ready["led"])
                    while not self.closed.is_set():
                        try:
                            command = self.commands.get(timeout=2)
                        except Empty:
                            if not self.closed.is_set():
                                self._observe_state(lab)
                            continue
                        if command == "stop" or self.closed.is_set():
                            break
                        self._checkpoint(output, "applying")
                        self._press(lab, output, run_id)
                finally:
                    self._set(phase="stopping")
            self._checkpoint(output, "stopped")
            self._set(phase="stopped", connected=[], led=None)
        except (OSError, RuntimeError, ValueError, SubprocessError) as error:
            if output.is_dir():
                self._checkpoint(output, "failed")
            self._set(phase="failed", connected=[], led=None, error=str(error),
                      cleanup_failed=isinstance(error, CleanupError))
        finally:
            with self.lock:
                while not self.commands.empty():
                    self.commands.get_nowait()
