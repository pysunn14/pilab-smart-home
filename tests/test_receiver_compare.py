import unittest

from lab.receiver_compare import legacy_samples


class ReceiverCompareTest(unittest.TestCase):
    @staticmethod
    def inputs():
        return [{"sample": 1, "timestamp_ns": 100}, {"sample": 2, "timestamp_ns": 600}]

    def test_repeated_baseline_values_are_counted_as_one_receipt(self):
        readings = [{"timestamp_ns": 120}, {"timestamp_ns": 650}]
        receipts = [{"timestamp_ns": t, "value": v} for t, v in
                    [(50, 1), (110, 1), (150, 0), (250, 0), (700, 1), (800, 1)]]
        samples = legacy_samples(self.inputs(), {1: 200, 2: 710}, readings, receipts, 400)
        self.assertEqual([s["outcome"] for s in samples], ["received", "received"])
        self.assertEqual([s["latency_ns"] for s in samples], [50, 100])

    def test_late_toggle_does_not_shift_the_next_input(self):
        readings = [{"timestamp_ns": 120}, {"timestamp_ns": 650}]
        receipts = [{"timestamp_ns": t, "value": v} for t, v in
                    [(50, 1), (630, 0), (700, 1)]]
        samples = legacy_samples(self.inputs(), {1: 200, 2: 710}, readings, receipts, 400)
        self.assertEqual([s["outcome"] for s in samples], ["missing", "ambiguous"])
        self.assertTrue(all(s["latency_ns"] is None for s in samples))

    def test_polling_miss_does_not_make_the_next_valid_toggle_ambiguous(self):
        readings = [{"timestamp_ns": 650}]
        receipts = [{"timestamp_ns": 50, "value": 1}, {"timestamp_ns": 700, "value": 0}]
        samples = legacy_samples(self.inputs(), {1: 200, 2: 710}, readings, receipts, 400)
        self.assertEqual([s["outcome"] for s in samples], ["missing", "received"])
        self.assertEqual(samples[1]["latency_ns"], 100)
