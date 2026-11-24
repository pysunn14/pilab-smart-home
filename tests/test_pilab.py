import subprocess
import unittest
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "pilab"


class PiLabTest(unittest.TestCase):
    def test_default_invocation_shows_banner_and_web_command(self):
        result = subprocess.run([CLI], capture_output=True, text=True, check=True)
        self.assertIn(r"/_/   /___/_____/_/  |_/_____/", result.stdout)
        self.assertIn("pilab web", result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_invalid_port_returns_an_error(self):
        result = subprocess.run([CLI, "web", "--port", "65536"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"65535", result.stderr)


if __name__ == "__main__":
    unittest.main()
