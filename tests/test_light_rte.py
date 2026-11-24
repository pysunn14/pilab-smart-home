import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LightRteTest(unittest.TestCase):
    def test_reports_only_successful_gpio_writes(self):
        application = ROOT / "lightpi" / "application"
        rte = ROOT / "lightpi" / "rte"
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "light_rte_test"
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", application, "-I", rte, application / "light_swc.c", application / "blind_swc.c",
                 rte / "light_rte.c",
                 ROOT / "tests" / "light_rte_test.c", "-o", binary],
                check=True,
            )
            subprocess.run([binary], check=True, timeout=10)
