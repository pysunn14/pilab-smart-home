import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class LabTest(unittest.TestCase):
    def test_cli_exposes_smoke_command(self):
        output = subprocess.check_output([ROOT / "pilab", "lab", "smoke", "--help"], text=True)
        self.assertIn("--legacy-repo", output)
        self.assertIn("--output", output)

    def test_light_ack_delay_is_available_from_pilab(self):
        output = subprocess.check_output([ROOT / "pilab", "lab", "light-ack-delay", "--help"], text=True)
        self.assertIn("--output", output)

    def test_light_ack_retry_requires_an_explicit_timeout(self):
        command = [ROOT / "pilab", "lab", "light-ack-retry"]
        help_text = subprocess.check_output([*command, "--help"], text=True)
        self.assertIn("--drop-count {1,2}", help_text)
        result = subprocess.run([*command, "--drop-count", "1"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("required: --timeout-ms", result.stderr)

    def test_delayed_ack_rejects_premature_second_send(self):
        from lab.light_ack_delay import verify_held

        first = "mainpi/0123456789abcdef0123456789abcdef/1"
        second = "mainpi/0123456789abcdef0123456789abcdef/2"
        sent = f'light_command_sent commandId="{first}" on=true\n'
        verify_held(first, sent, [True])
        with self.assertRaises(RuntimeError):
            verify_held(first, sent + f'light_command_sent commandId="{second}" on=false\n', [True])

    def test_light_ack_recovery_requires_both_replies_to_be_dropped(self):
        result = subprocess.run([ROOT / "pilab", "lab", "light-ack-retry", "--drop-count", "1",
                                 "--timeout-ms", "1500", "--recover"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--recover에는 --drop-count 2가 필요합니다", result.stderr)

    def test_staging_is_deterministic_and_excludes_credentials(self):
        legacy = importlib.import_module("lab.legacy")
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory)
            legacy.stage(ROOT, staged / "a")
            legacy.stage(ROOT, staged / "b")
            left = {p.relative_to(staged / "a"): p.read_bytes() for p in (staged / "a").rglob("*") if p.is_file()}
            right = {p.relative_to(staged / "b"): p.read_bytes() for p in (staged / "b").rglob("*") if p.is_file()}
            self.assertEqual(left, right)
            self.assertEqual(json.loads(left[Path("legacy.json")]), legacy.REVISIONS)
            self.assertFalse(any(p.suffix in {".p12", ".jks", ".pem"} for p in left))
            self.assertFalse(any(p.name == "FirebaseConfig.java" for p in left))
            self.assertIn(b"wss://", left[Path("pi/fire/bridge.py")])
            self.assertIn(b'\"legacy_dht\"', left[Path("pi/fire/node.c")])

    def test_transition_parser_counts_only_new_output(self):
        smoke = importlib.import_module("lab.smoke")
        records = [
            {"event": "gpio", "pin": 13, "value": 1},
            {"event": "gpio", "pin": 18, "value": 0},
            {"event": "gpio", "pin": 18, "value": 0},
            {"event": "gpio", "pin": 18, "value": 1},
        ]
        self.assertEqual(smoke.led_transitions(records, initial=1), [0, 1])

    def test_button_receiver_is_staged_only_in_the_spring_variant(self):
        legacy = importlib.import_module("lab.legacy")
        receiver = importlib.import_module("lab.spring_receiver")
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline"
            variant = Path(directory) / "variant"
            legacy.stage_spring(ROOT, baseline)
            receiver.stage_receiver(ROOT, variant)
            handler = Path(legacy.SPRING_JAVA) / receiver.HANDLER
            device_receiver = Path(legacy.SPRING_JAVA) / receiver.RECEIVER
            self.assertNotIn("DeviceMessageReceiver.handle", (baseline / handler).read_text())
            self.assertFalse((baseline / device_receiver).exists())
            modern = Path(legacy.SPRING_JAVA) / "handler" / "DeviceWebSocketHandler.java"
            self.assertIn("DeviceMessageReceiver.handle", (variant / modern).read_text())
            self.assertTrue((variant / device_receiver).is_file())

    def test_current_spring_stages_without_legacy_git_history(self):
        receiver = importlib.import_module("lab.spring_receiver")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / "checkout"
            shutil.copytree(ROOT / "spring", checkout / "spring", ignore=shutil.ignore_patterns(".gradle", "build"))
            source = checkout / "spring" / "src" / "main" / "java"
            staged = root / "build"
            receiver.stage_receiver(checkout, staged)
            for path in source.rglob("*.java"):
                relative = path.relative_to(checkout / "spring")
                self.assertEqual((staged / relative).read_bytes(), path.read_bytes())
            self.assertTrue((staged / "build.gradle").is_file())
            self.assertTrue((staged / "Dockerfile").is_file())
            probe = Path(receiver.SPRING_JAVA) / "handler" / "ReceiverLabSessionProbe.java"
            self.assertFalse((checkout / "spring" / probe).exists())
            self.assertTrue((staged / probe).is_file())


if __name__ == "__main__":
    unittest.main()
