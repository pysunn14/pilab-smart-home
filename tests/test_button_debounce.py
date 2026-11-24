import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAINPI = ROOT / "mainpi"
BSW = MAINPI / "bsw" / "ecu_abstraction"
SERVICES = BSW.parent / "services"
MCAL = BSW.parent / "mcal"


class ButtonDebounceTest(unittest.TestCase):
    def test_c_state_machine(self):
        self.run_c_test("button_debounce_test")

    def test_input_task_requests_the_debounce_deadline(self):
        self.run_c_test("button_input_task_test", SERVICES / "button_input_task.c")

    def test_button_ports_emit_only_presses_and_propagate_rejections(self):
        rte = MAINPI / "rte"
        application = MAINPI / "application"
        self.run_c_test(
            "button_rte_test", "-I", rte, "-I", application,
            rte / "button_rte.c", application / "button_swc.c",
        )

    def test_button_tx_queue_preserves_pending_presses_in_order(self):
        self.run_c_test("button_tx_queue_test", "-pthread", SERVICES / "button_tx_queue.c")

    @unittest.skipUnless(sys.platform == "linux", "requires Linux ppoll")
    def test_linux_wait_with_a_pipe_and_monotonic_deadline(self):
        self.run_c_test("button_input_linux_test", "-pthread", SERVICES / "button_input_linux.c")

    @unittest.skipUnless(sys.platform == "linux", "requires Linux GPIO")
    def test_gpio_adapter_preserves_levels_and_timestamps(self):
        if shutil.which("pkg-config") is None or subprocess.run(
            ["pkg-config", "--atleast-version=2.0", "libgpiod"], check=False
        ).returncode != 0:
            self.skipTest("requires libgpiod 2 development files")
        cflags = shlex.split(subprocess.check_output(
            ["pkg-config", "--cflags", "libgpiod"], text=True
        ))
        libraries = shlex.split(subprocess.check_output(
            ["pkg-config", "--libs", "libgpiod"], text=True
        ))
        wrapped = [
            "gpiod_line_request_read_edge_events",
            "gpiod_edge_event_buffer_get_event",
            "gpiod_edge_event_get_event_type",
            "gpiod_edge_event_get_timestamp_ns",
            "gpiod_edge_event_get_line_seqno",
        ]
        self.run_c_test(
            "button_gpio_input_test", "-I", MCAL, *cflags, MCAL / "button_gpio_input.c",
            link_flags=[*(f"-Wl,--wrap={name}" for name in wrapped), *libraries],
        )

    def run_c_test(self, name, *sources, link_flags=()):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / name
            subprocess.run(
                ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", BSW, "-I", SERVICES, BSW / "button_debounce.c",
                 *sources, ROOT / "tests" / f"{name}.c", *link_flags, "-o", binary],
                check=True,
            )
            subprocess.run([binary], check=True, timeout=10)
