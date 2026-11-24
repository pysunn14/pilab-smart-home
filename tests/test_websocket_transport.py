import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WebSocketTransportTest(unittest.TestCase):
    def test_transport_contract(self):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            self.skipTest("requires a C compiler and pkg-config")
        if subprocess.run(["pkg-config", "--atleast-version=7.86.0", "libcurl"]).returncode:
            self.skipTest("requires libcurl 7.86 development files")

        transport = ROOT / "common/bsw/services"
        flags = shlex.split(subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libcurl"], text=True))
        sources = [ROOT / "tests/websocket_transport_test.c", transport / "websocket_transport.c"]
        include = ["-I", transport]
        if sys.platform == "linux":
            mainpi = ROOT / "mainpi/bsw/services"
            include.extend(["-I", mainpi])
            sources.append(mainpi / "button_websocket.c")
        with tempfile.TemporaryDirectory() as work:
            binary = Path(work) / "websocket-transport-test"
            subprocess.run([
                "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Dcurl_ws_send=test_curl_ws_send",
                "-Dcurl_ws_recv=test_curl_ws_recv",
                *include, *sources, *flags, "-o", binary,
            ], check=True)
            subprocess.run([binary], check=True, timeout=8)
