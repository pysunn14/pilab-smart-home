import unittest

from lab.p0_metrics import burst_result, isolated_result, recovery_result, resource_result


class P0MetricsTest(unittest.TestCase):
    def test_burst_never_assigns_individual_latency_without_ids(self):
        result = burst_result([1, 0], [1])
        self.assertEqual(result["sequence_matches"], False)
        self.assertEqual(result["output_shortfall"], 1)
        self.assertIsNone(result["per_input_latency_ms"])
        self.assertIsNone(result["per_input_loss_rate"])

    def test_sensor_output_requires_every_expected_actuator(self):
        required = {"alarm": 1, "servo": 2500}
        rows = [{"channel": "alarm", "value": 1, "timestamp_ns": 2_000_000}]
        self.assertEqual(isolated_result(1_000_000, required, rows)["outcome"], "missing")
        rows.append({"channel": "servo", "value": 2500, "timestamp_ns": 4_000_000})
        self.assertEqual(isolated_result(1_000_000, required, rows)["latency_ms"], 3)
        rows.append({"channel": "servo", "value": 500, "timestamp_ns": 5_000_000})
        self.assertEqual(isolated_result(1_000_000, required, rows)["outcome"], "extra_transition")

    def test_open_socket_without_functional_probe_is_not_recovery(self):
        rows = [{"valid": True, "detected_ms": 15, "reconnected_ms": 250, "recovered_ms": None}]
        summary = recovery_result(rows, limit_ms=10000)
        self.assertEqual(summary["recovery_success_rate"], 0)
        self.assertEqual(summary["within_target_rate"], 0)
        self.assertEqual(summary["slo"]["status"], "fail")

    def test_soak_tracks_scheduled_denominator_and_separate_pid_epochs(self):
        def sample(slot, start):
            return {"slot": slot, "processes": {"spring/java": {
                "pid": 1, "start_ticks": start, "rss_kib": slot * 10, "fds": slot, "threads": 3,
            }}, "connected": 4}
        result = resource_result([sample(1, 100), sample(2, 200)], 3, {"spring/java"}, 0)
        self.assertEqual(result["survival_rate"], 2 / 3)
        self.assertEqual(result["connected_rate"], 8 / 12)
        self.assertEqual(result["process_restarts"], 1)
        self.assertEqual(len(result["processes"]["spring/java"]), 2)


if __name__ == "__main__":
    unittest.main()
