import unittest

from lab.receiver_benchmark import analyze


class ReceiverBenchmarkTest(unittest.TestCase):
    def test_missing_input_does_not_shift_the_next_receipt(self):
        inputs = [{"sample": 1, "timestamp_ns": 100_000_000},
                  {"sample": 2, "timestamp_ns": 200_000_000}]
        presses = [{"seq": 1, "edge_ns": 200_000_000}]
        receipts = [{"seq": 1, "timestamp_ns": 213_000_000}]

        result, samples = analyze(inputs, presses, receipts, 300_000_000)

        self.assertEqual([item["outcome"] for item in samples], ["missing", "received"])
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertEqual(result["latency_ms"], {"samples": 1, "p50": 13, "p95": 13, "p99": 13})

    def test_duplicate_and_impossible_time_are_excluded_from_latency(self):
        inputs = [{"sample": 1, "timestamp_ns": 100}, {"sample": 2, "timestamp_ns": 200},
                  {"sample": 3, "timestamp_ns": 300}]
        presses = [{"seq": 1, "edge_ns": 100}, {"seq": 2, "edge_ns": 200}, {"seq": 3, "edge_ns": 300}]
        receipts = [{"seq": 1, "timestamp_ns": 120}, {"seq": 1, "timestamp_ns": 140},
                    {"seq": 2, "timestamp_ns": 199}, {"seq": 3, "timestamp_ns": 400},
                    {"seq": 4, "timestamp_ns": 150}]

        result, samples = analyze(inputs, presses, receipts, 350)

        self.assertEqual([item["outcome"] for item in samples], ["duplicate", "invalid_time", "missing"])
        self.assertEqual(result["counts"]["unmatched_receipts"], 1)
        self.assertEqual(result["counts"]["after_observation"], 1)
        self.assertEqual(result["latency_ms"]["samples"], 0)

    def test_reused_sequence_makes_attribution_invalid(self):
        with self.assertRaisesRegex(ValueError, "순번"):
            analyze([], [{"seq": 1, "edge_ns": 100}, {"seq": 1, "edge_ns": 200}], [], 300)
