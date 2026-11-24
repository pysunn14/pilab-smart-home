"""Build and observe a MainPi-to-Spring receiver experiment."""

import json
import re
import shutil

from .legacy import HERE, SPRING_JAVA, stage as stage_baseline, substitute
from .spring_receiver import HANDLER, stage_receiver


PROFILE = "button-receiver"
LEGACY_PROFILE = "legacy-button-receiver"
LIGHT_ACK_DELAY_PROFILE = "light-ack-delay"
LIGHT_ACK_RETRY_PROFILE = "light-ack-retry"
LIGHT_RECONNECT_PROFILE = "light-reconnect"
LIGHT_BENCHMARK_PROFILE = "light-benchmark"
LIGHT_ROOM_PROFILE = "light-room"
INTEGRATED_PROFILE = "integrated-smoke"
ROOM_PROFILE = "room"
AIR_DUST_PROFILE = "air-dust-smoke"
AIR_SERVO_PROFILE = "air-servo-smoke"
AIR_AUTO_SERVO_PROFILE = "air-auto-servo-smoke"
FIRE_SENSOR_PROFILE = "fire-sensor-smoke"
FIRE_ROOM_PROFILE = "fire-room"
FIRE_ALARM_PROFILE = "fire-alarm-smoke"
TELEMETRY_ROLES = {
    AIR_DUST_PROFILE: "air", AIR_SERVO_PROFILE: "air", AIR_AUTO_SERVO_PROFILE: "air",
    FIRE_SENSOR_PROFILE: "fire", FIRE_ROOM_PROFILE: "fire", FIRE_ALARM_PROFILE: "fire",
}
RECEIVED = re.compile(
    r'received_ns=(\d+) button_received nodeId="mainpi" runId=([0-9a-f]{32}) seq=(\d+)$', re.MULTILINE
)
LEGACY_RECEIVED = re.compile(r"^legacy_button_received received_ns=(\d+) value=([01])$", re.MULTILINE)


def stage_websocket_transport(repo, build):
    shared = "common/bsw/services"
    destination = build / shared
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("websocket_transport.c", "websocket_transport.h", "websocket.mk"):
        shutil.copyfile(repo / shared / name, destination / name)
    shutil.copyfile(repo / "common/Makefile", build / "common/Makefile")


def stage(repo, build, *, drop_first_light_command=False):
    spring = build / "spring"
    stage_receiver(repo, spring)
    if drop_first_light_command:
        stage_light_command_loss(spring)
    pi = build / "pi"
    mainpi = repo / "mainpi"
    sources = [mainpi / "Makefile"]
    for pattern in ("*.c", "application/*.[ch]", "rte/*.[ch]", "bsw/*/*.[ch]"):
        sources.extend(mainpi.glob(pattern))
    for source in sources:
        target = pi / "mainpi" / source.relative_to(mainpi)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    stage_websocket_transport(repo, pi)
    shutil.copyfile(repo / "tests/button_monitor_gpio_stub.c", pi / "button_monitor_gpio_stub.c")
    shutil.copyfile(HERE / "receiver_node.py", pi / "receiver_node.py")
    shutil.copyfile(HERE / "benchmark_workload.py", pi / "benchmark_workload.py")
    shutil.copyfile(HERE / "Dockerfile.receiver", pi / "Dockerfile")


def stage_telemetry(repo, build, role):
    stage_receiver(repo, build / "spring")
    stage_telemetry_node(repo, build, role)


def stage_telemetry_node(repo, build, role):
    node = build / role
    source = f"{role}pi"
    destination = node / source
    destination.mkdir(parents=True)
    for pattern in ("Makefile", "*.c", "*.h"):
        for path in (repo / source).glob(pattern):
            shutil.copyfile(path, destination / path.name)
    stage_websocket_transport(repo, node)
    shared = "common/bsw/services"
    for name in ("telemetry_publisher.c", "telemetry_publisher.h", "telemetry.mk",
                 "virtual_actuator.c", "virtual_actuator.h"):
        shutil.copyfile(repo / shared / name, node / shared / name)
    shutil.copyfile(HERE / f"Dockerfile.{role}", node / "Dockerfile")


def stage_light_command_loss(spring):
    sender = spring / SPRING_JAVA / "handler" / "LightCommandSender.java"
    source = substitute(
        sender.read_text(encoding="utf-8"),
        r"^    private void send\(Pending attempt, boolean retry\) \{$",
        '    private boolean dropFirstLightCommand = "1".equals(System.getenv("PILAB_DROP_FIRST_LIGHT_COMMAND"));\n\n'
        "    private void send(Pending attempt, boolean retry) {",
    )
    # Report send success to the sender while discarding the first command before the WebSocket write.
    source = substitute(
        source,
        r"^        if \(!write\(attempt, new TextMessage\(frame\.toString\(\)\)\)\) \{$",
        "        if (dropFirstLightCommand) {\n"
        "            dropFirstLightCommand = false;\n"
        '            LOG.info(() -> "lab_light_command_dropped commandId=" + commandId);\n'
        "        } else if (!write(attempt, new TextMessage(frame.toString()))) {",
    )
    sender.write_text(source, encoding="utf-8")


def stage_light_ack_delay(repo, build):
    light = build / "light"
    sources = repo / "lightpi"
    for pattern in ("Makefile", "*.c", "application/*.[ch]", "rte/*.[ch]", "bsw/*/*.[ch]"):
        for source in sources.glob(pattern):
            target = light / "lightpi" / source.relative_to(sources)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    stage_websocket_transport(repo, light)
    shared = "common/bsw/services"
    for name in ("telemetry_publisher.c", "telemetry_publisher.h", "telemetry.mk",
                 "virtual_actuator.c", "virtual_actuator.h"):
        shutil.copyfile(repo / shared / name, light / shared / name)
    shutil.copyfile(repo / "tests/light_gpio_stub.c", light / "light_gpio_stub.c")
    shutil.copyfile(HERE / "light_ack_gate.c", light / "light_ack_gate.c")
    shutil.copyfile(HERE / "Dockerfile.light-ack-delay", light / "Dockerfile")


def stage_legacy(repo, build):
    stage_baseline(repo, build)
    handler = build / "spring" / SPRING_JAVA / HANDLER
    source = substitute(
        handler.read_text(encoding="utf-8"),
        r"^                // key-value 쌍 순회$",
        "                long receivedNs = System.nanoTime();\n"
        "                Number button = json.get(\"buttonSignal\");\n"
        "                var remote = session.getRemoteAddress();\n"
        "                if (button != null && remote != null && remote.getAddress() != null\n"
        "                        && remote.getAddress().getHostAddress().equals(IP.MainPiIp)) {\n"
        "                    System.out.printf(\"legacy_button_received received_ns=%d value=%d%n\",\n"
        "                            receivedNs, button.doubleValue() == 1.0 ? 1 : 0);\n"
        "                }\n\n"
        "                // key-value 쌍 순회",
    )
    handler.write_text(source, encoding="utf-8")


def legacy_receipts(lab):
    return [{"value": int(value), "timestamp_ns": int(timestamp)}
            for timestamp, value in LEGACY_RECEIVED.findall(lab.logs("spring"))]


def node_command(lab, *args):
    return lab.run("exec", "-T", "main", "python", "/app/receiver_node.py", *args)


def main_events(lab):
    return [json.loads(line) for line in lab.logs("main").splitlines() if line.startswith('{"event":')]


def parse_button_receipts(log):
    return [{"seq": int(sequence), "timestamp_ns": int(timestamp),
             "commandId": f"mainpi/{run_id}/{sequence}"}
            for timestamp, run_id, sequence in RECEIVED.findall(log)]


def spring_receipts(lab):
    return [{"seq": row["seq"], "timestamp_ns": row["timestamp_ns"]}
            for row in parse_button_receipts(lab.logs("spring"))]


def check_ready(lab, main_address):
    def ready():
        lab.check_processes()
        if not any(event["event"] == "ready" for event in main_events(lab)):
            return []
        return json.loads(node_command(lab, "probe"))["sessions"]

    connected = lab.wait(ready, lambda sessions: main_address in sessions, "MainPi 가상 GPIO와 WebSocket 준비")
    return {"connected": connected, "led": None}


def check_button(lab, sequence, observe):
    previous = len(main_events(lab))
    observe("INPUT", json.loads(node_command(lab, "edge", "0")))

    def received():
        lab.check_processes()
        return [receipt["seq"] for receipt in spring_receipts(lab)]

    observed = lab.wait(received, lambda values: len(values) >= sequence,
                        f"Spring의 버튼 메시지 #{sequence} 수신", timeout=8)
    if observed != list(range(1, sequence + 1)):
        raise RuntimeError(f"Spring 버튼 수신 순번이 예상과 다릅니다. 관측: {observed}")

    node_command(lab, "edge", "1")
    lab.wait(lambda: main_events(lab)[previous:], lambda events: any(
        event["event"] == "button_released" for event in events
    ), "MainPi의 버튼 떼기 감지", timeout=8)
    return sequence
