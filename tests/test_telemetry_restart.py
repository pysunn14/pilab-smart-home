import unittest

from lab.telemetry_restart import validate_reconnect


def attempt(number, delay):
    return {"event": "reconnect_attempt", "attempt": number, "delay_ms": delay}


def failed(number):
    return {"event": "reconnect_failed", "attempt": number, "error": 7}


class TelemetryRestartTest(unittest.TestCase):
    def setUp(self):
        self.events = [{"event": "connection_lost"}, attempt(1, 0), failed(1), attempt(2, 1000), failed(2),
                       attempt(3, 2000)]

    def test_third_attempt_can_recover(self):
        validate_reconnect(self.events + [{"event": "reconnected"}], "recovered", 0)

    def test_exhaustion_requires_three_failed_attempts_and_a_process_error(self):
        exhausted = self.events + [failed(3), {"event": "reconnect_exhausted"}]
        validate_reconnect(exhausted, "retry_exhausted", 1)
        with self.assertRaises(RuntimeError):
            validate_reconnect(exhausted, "retry_exhausted", 0)

    def test_process_exit_without_retry_evidence_is_an_experiment_failure(self):
        with self.assertRaises(RuntimeError):
            validate_reconnect(self.events[:3], "retry_exhausted", 1)

    def test_mismatched_attempt_or_terminal_event_is_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_reconnect(self.events[:-1] + [attempt(4, 2000), {"event": "reconnected"}], "recovered", 0)
        with self.assertRaises(RuntimeError):
            validate_reconnect(self.events + [failed(3), {"event": "reconnected"}], "recovered", 0)
