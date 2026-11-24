"""Deterministic sensor inputs shared by the PiLab web dashboard and CLI."""

from os import link
from pathlib import Path
from sys import float_info
from tempfile import NamedTemporaryFile
from threading import Condition


SENSOR_DEFAULTS = {
    "air": {"dust": 23.5, "temperature": 24.1, "humidity": 42},
    "fire": {"infra": 1000, "gas": 10},
    "light": {"light": 500},
}
# AirPi and Spring use this exclusive bound for inputs rounded to hundredths.
MAX_READING = (2 ** 63) / 100.0


class RoomEnvironment:
    def __init__(self, room_file=None, *, node="air"):
        self._values = SENSOR_DEFAULTS[node].copy()
        self._fields = tuple(self._values)
        self._revision = 0
        self._changed = Condition()
        self._room_file = room_file
        if room_file is not None:
            self._write_room_file(self._values, initialize=True)

    def _write_room_file(self, values, *, initialize=False):
        target = self._room_file
        with NamedTemporaryFile(mode="w", encoding="ascii", dir=target.parent, delete=False) as temporary:
            pending = Path(temporary.name)
            try:
                temporary.write(_sample_line(values, self._fields) + "\n")
                temporary.close()
                # Publish one whole snapshot. The first write must not take over another producer's file.
                if initialize:
                    link(pending, target)
                else:
                    pending.replace(target)
            finally:
                pending.unlink(missing_ok=True)

    def snapshot(self):
        with self._changed:
            return {"values": self._values.copy(), "revision": self._revision}

    def wait_for_change(self, after, *, timeout):
        with self._changed:
            if not self._changed.wait_for(lambda: self._revision > after, timeout=timeout):
                return None
            return self.snapshot()

    def update(self, patch):
        if not isinstance(patch, dict) or not patch or patch.keys() - set(self._fields):
            raise ValueError("선택한 장치의 센서 입력 중 바꿀 값을 지정하세요.")
        readings = {}
        for name, value in patch.items():
            if (type(value) not in (int, float) or not -MAX_READING < value < MAX_READING
                    or abs(float(value)) >= MAX_READING or 0 < abs(value) < float_info.min):
                raise ValueError(f"{name}의 센서값 범위를 확인하세요.")
            readings[name] = float(value)
            if name in {"dust", "infra", "gas", "light"} and readings[name] < 0:
                raise ValueError(f"{name}은 0 이상이어야 합니다.")
        if "humidity" in readings and not 0 <= readings["humidity"] <= 100:
            raise ValueError("습도는 0~100%로 지정하세요.")

        with self._changed:
            if any(self._values[name] != value for name, value in readings.items()):
                values = {**self._values, **readings}
                if self._room_file is not None:
                    self._write_room_file(values)
                self._values = values
                self._revision += 1
                self._changed.notify_all()
            return self.snapshot()


def sensor_sample(room, node):
    return _sample_line(room["values"], SENSOR_DEFAULTS[node])


def _sample_line(values, fields):
    readings = (values[name] for name in fields)
    return " ".join(str(int(value)) if int(value) == value else str(value) for value in readings)
