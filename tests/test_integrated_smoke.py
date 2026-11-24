import unittest

from lab.integrated_smoke import validate_light, validate_sensor_samples
from lab.telemetry_smoke import SAMPLES


COMMAND_ID = "mainpi/0123456789abcdef0123456789abcdef/1"


def sensor_receipts():
    return [
        {"nodeId": f"{role}pi", "runId": role_id * 32, "seq": sequence,
         "values": SAMPLES[role][sequence - 1], "receivedAt": "2026-11-23T00:00:00Z"}
        for sequence in (1, 2)
        for role, role_id in (("fire", "f"), ("air", "a"))
    ]


class IntegratedSmokeTest(unittest.TestCase):
    def test_uses_sequence_order_per_node_for_interleaved_receipts(self):
        validate_sensor_samples(sensor_receipts(), 2)

    def test_rejects_missing_duplicated_or_restarted_sensor_streams(self):
        samples = sensor_receipts()
        for invalid in (samples[:-1], samples[:3] + [samples[2]], samples[:3] + [{**samples[3], "runId": "b" * 32}]):
            with self.subTest(receipts=invalid):
                with self.assertRaises(RuntimeError):
                    validate_sensor_samples(invalid, 2)

    def test_requires_one_gpio_write_and_matching_application_reply(self):
        events = [("sent", COMMAND_ID, "true", ""), ("applied", COMMAND_ID, "true", "CONFIRMED")]
        gpio = [{"offset": 18, "on": True, "commandId": COMMAND_ID}]
        validate_light(COMMAND_ID, events, gpio, [])
        for invalid, pending in ((gpio * 2, []), (gpio, [COMMAND_ID]), ([{**gpio[0], "on": False}], [])):
            with self.subTest(gpio=invalid, pending=pending):
                with self.assertRaises(RuntimeError):
                    validate_light(COMMAND_ID, events, invalid, pending)
        with self.assertRaises(RuntimeError):
            validate_light(COMMAND_ID, events[:1], gpio, [])
