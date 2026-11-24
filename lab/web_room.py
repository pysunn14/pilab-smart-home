"""Own one web-started connected room and the lifetime of its Docker resources."""

from datetime import datetime, timezone
from subprocess import SubprocessError
from threading import Event, Lock, Thread
from uuid import uuid4

from .connected_room import open_room
from .session import SessionConflict
from .smoke import CleanupError


class WebRoom:
    def __init__(self, repo, runs, prefix):
        self.repo = repo
        self.runs = runs
        self.prefix = prefix
        self.lock = Lock()
        self.stop = Event()
        self.worker = None
        self.room = None
        self.phase = "idle"
        self.step = 0
        self.run_id = None
        self.error = None
        self.cleanup_failed = False
        self.closed = False

    def snapshot(self):
        with self.lock:
            state = self.room.session.snapshot() if self.room is not None and self.phase == "running" else {}
            return {"phase": self.phase, **state, "mode": "room", "lifecycle": True,
                    "step": self.step, "runId": self.run_id, "error": self.error,
                    "cleanupFailed": self.cleanup_failed}

    def room_for(self, node):
        with self.lock:
            return self.room.rooms[node] if self.phase == "running" else None

    def request(self, action):
        with self.lock:
            if self.closed:
                raise SessionConflict("통합 작업대가 종료 중입니다.")
            if action == "start":
                if self.worker is not None or self.cleanup_failed:
                    raise SessionConflict("이전 실험의 정리가 끝난 뒤 시작할 수 있습니다.")
                self.stop.clear()
                self.phase = "starting"
                self.step = 0
                self.error = None
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                self.run_id = f"room-{stamp}-{uuid4().hex[:8]}"
                self.worker = Thread(target=self._run, args=(self.run_id,), name="pilab-web-room")
                self.worker.start()
            elif action == "stop":
                if self.phase not in ("starting", "running"):
                    raise SessionConflict("현재 정지할 수 있는 실험이 없습니다.")
                self.phase = "stopping"
                self.stop.set()
            elif action == "button":
                if self.phase != "running":
                    raise SessionConflict("네 Pi가 연결된 뒤 버튼을 누를 수 있습니다.")
                self.room.session.request(action)
            else:
                raise ValueError(f"알 수 없는 조작입니다. {action}")

    def _progress(self, step):
        with self.lock:
            self.step = step

    def _run(self, run_id):
        phase, message, cleanup_failed = "stopped", None, False
        try:
            with open_room(self.repo, self.runs / run_id, self.prefix, self._progress) as room:
                with self.lock:
                    if not self.stop.is_set():
                        self.room = room
                        self.phase = "running"
                try:
                    room.serve_until(self.stop)
                finally:
                    with self.lock:
                        self.phase = "stopping"
                        self.room = None
        except (OSError, RuntimeError, ValueError, SubprocessError) as error:
            phase, message = "failed", str(error)
            cleanup_failed = isinstance(error, CleanupError)
        finally:
            with self.lock:
                self.phase = phase
                self.error = message
                self.cleanup_failed = cleanup_failed
                self.worker = None

    def close(self):
        with self.lock:
            self.closed = True
            worker = self.worker
            if worker is not None:
                self.phase = "stopping"
                self.stop.set()
        if worker is not None:
            worker.join()
