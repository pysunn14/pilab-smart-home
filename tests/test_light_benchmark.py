import unittest

from lab.light_benchmark import analyze


MS = 1_000_000


class LightBenchmarkTest(unittest.TestCase):
    def test_matches_by_command_id_even_when_output_arrives_in_a_later_slot(self):
        inputs = [{"sample": n, "timestamp_ns": n * 200 * MS} for n in (1, 2, 3)]
        presses = [{"seq": n, "edge_ns": n * 200 * MS} for n in (1, 2, 3)]
        receipts = [{"seq": n, "commandId": f"mainpi/run/{n}"} for n in (1, 2, 3)]
        gpio = [{"commandId": "mainpi/run/2", "timestamp_ns": 450 * MS, "on": False},
                {"commandId": "mainpi/run/1", "timestamp_ns": 1250 * MS, "on": True},
                {"commandId": "other/unknown/7", "timestamp_ns": 1400 * MS, "on": False}]
        report, samples = analyze(inputs, presses, receipts, gpio, 3)
        self.assertEqual([row["outcome"] for row in samples], ["late", "on_time", "missing"])
        self.assertEqual(report["counts"]["missing"], 1)
        self.assertEqual(report["latency_ms"]["p99"], 1050)
        self.assertIn("unmatched_gpio_writes", report["issues"])
        self.assertIn("out_of_order_gpio_writes", report["issues"])

    def test_reports_duplicate_and_wrong_output_and_incomplete_injection(self):
        inputs = [{"sample": n, "timestamp_ns": n * 200 * MS} for n in (1, 2)]
        presses = [{"seq": n, "edge_ns": n * 200 * MS} for n in (1, 2)]
        receipts = [{"seq": n, "commandId": f"mainpi/run/{n}"} for n in (1, 2)]
        gpio = [{"commandId": "mainpi/run/1", "timestamp_ns": 250 * MS, "on": True},
                {"commandId": "mainpi/run/1", "timestamp_ns": 260 * MS, "on": True},
                {"commandId": "mainpi/run/2", "timestamp_ns": 450 * MS, "on": True}]
        report, samples = analyze(inputs, presses, receipts, gpio, 3)
        self.assertEqual([row["outcome"] for row in samples], ["duplicate", "incorrect"])
        self.assertEqual(report["latency_ms"]["samples"], 0)
        self.assertEqual(report["counts"]["not_injected"], 1)

    def test_skipped_slot_does_not_toggle_the_expected_gpio_state(self):
        inputs = [{"sample": 2, "timestamp_ns": 400 * MS}]
        presses = [{"seq": 1, "edge_ns": 400 * MS}]
        receipts = [{"seq": 1, "commandId": "mainpi/run/1"}]
        gpio = [{"commandId": "mainpi/run/1", "timestamp_ns": 425 * MS, "on": True}]
        report, samples = analyze(inputs, presses, receipts, gpio, 2)
        self.assertEqual(samples[0]["outcome"], "on_time")
        self.assertIn("incomplete_injection", report["issues"])
