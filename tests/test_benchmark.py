import unittest
from unittest.mock import Mock

from lab.benchmark import runtime
from lab.benchmark_result import classify, summarize
from lab.benchmark_workload import schedule


MS = 1_000_000


def input_at(offset_ms):
    return {"event": "input", "timestamp_ns": offset_ms * MS}


def output_at(offset_ms, state):
    return {"event": "gpio", "pin": 18, "timestamp_ns": offset_ms * MS, "value": state}


class BenchmarkTest(unittest.TestCase):
    def test_different_linux_clock_namespaces_can_share_monotonic_time(self):
        import json

        clock = {"boot_id": "same-linux-kernel", "monotonic_offset": [0, 0]}
        main = {**clock, "clock_namespace": "time:[1]"}
        light = {**clock, "clock_namespace": "time:[2]"}
        lab = Mock()
        lab.run.side_effect = [json.dumps(main), json.dumps(light)]
        result = runtime(lab)
        self.assertEqual(result["clock_sources"], {"main": main, "light": light})

        lab.run.side_effect = [json.dumps(main), json.dumps({**light, "monotonic_offset": [1, 0]})]
        with self.assertRaises(RuntimeError):
            runtime(lab)

    def test_schedule_is_seeded_and_has_disjoint_slots(self):
        first = schedule(count=5, seed=17, slot_ms=5000, jitter_ms=750)
        self.assertEqual(first, schedule(count=5, seed=17, slot_ms=5000, jitter_ms=750))
        self.assertNotEqual(first, schedule(count=5, seed=18, slot_ms=5000, jitter_ms=750))
        for index, offset in enumerate(first):
            self.assertTrue(index * 5000 <= offset <= index * 5000 + 750)

    def test_late_and_missing_remain_in_the_denominator(self):
        inputs = [input_at(100), input_at(5100), input_at(10100)]
        outputs = [output_at(700, 1), output_at(7100, 0), output_at(7200, 0)]
        samples, unmatched = classify(inputs, outputs, initial=0, window_ns=4000 * MS, deadline_ns=1000 * MS)
        self.assertEqual([s["outcome"] for s in samples], ["on_time", "late", "missing"])
        self.assertFalse(unmatched)
        summary = summarize(samples, scheduled=3)
        self.assertEqual(summary["latency_ms"], {"samples": 2, "p50": 600, "p95": 2000, "p99": 2000})
        self.assertEqual(summary["rates"]["on_time"], 1 / 3)
        self.assertEqual(summary["rates"]["missing"], 1 / 3)
        self.assertEqual(summary["slo"]["on_time_99_pct"]["status"], "fail")

    def test_ambiguous_trace_does_not_pass_slo(self):
        inputs = [input_at(100), input_at(5100)]
        outputs = [output_at(400, 1), output_at(600, 0), output_at(4500, 1), output_at(5600, 0)]
        samples, unmatched = classify(inputs, outputs, initial=0, window_ns=4000 * MS, deadline_ns=1000 * MS)
        self.assertEqual([s["outcome"] for s in samples], ["duplicate", "unmatched"])
        self.assertEqual([point["timestamp_ns"] for point in unmatched], [4500 * MS, 5600 * MS])
        summary = summarize(samples, scheduled=2, issues=["ambiguous_outputs"])
        self.assertEqual(summary["slo"]["on_time_99_pct"]["status"], "inconclusive")

    def test_targets_require_enough_samples_to_pass(self):
        good = {"outcome": "on_time", "latency_ns": 900 * MS}
        one = summarize([good], scheduled=1)
        self.assertEqual(one["slo"]["accuracy_99_9_pct"]["status"], "insufficient_samples")
        many = summarize([good] * 1000, scheduled=1000)
        self.assertTrue(all(rule["status"] == "pass" for rule in many["slo"].values()))
        failed_injection = summarize([good], scheduled=2)
        self.assertEqual(failed_injection["counts"]["not_injected"], 1)
        self.assertEqual(failed_injection["slo"]["p99_1_s"]["status"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
