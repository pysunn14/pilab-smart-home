"""Prepare Docker build contexts from the pinned 2024 Git objects."""

import json
import re
import shutil
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
REVISIONS = {
    "main": "0e335fa6bd3b8bed43c083ed8c406314c0219a4d",
    "fire": "4cffa0ee8ccc609f8e6d59d7df632247b5d980f1",
    "air": "fa1d1bd90610a9eb08f01747d19ea3a6e8e3a0a3",
    "light": "d41312248558bb7da79c503be8445dbbe08c49c1",
    "spring": "e4b0ef9a8c8d26a2c69516477996e92a73cc7a5a",
}
PI_SOURCE = {
    "main": ("button.c", "main.py"),
    "fire": ("FireDetectionPi.c", "websocket.py"),
    "air": ("DustDetection.c", "webSocket.py"),
    "light": ("light.c", "websocket.py"),
}
SPRING_JAVA = "src/main/java/project/smarthome/"
GRADLE_CHECKSUM = "31c55713e40233a8303827ceb42ca48a47267a0ad4bab9177123121e71524c26"


def git(repo, *args):
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"원본 Git 저장소를 읽을 수 없습니다 ({repo}). {message}") from error


def substitute(source, pattern, result):
    updated, count = re.subn(pattern, lambda _: result, source, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"원본 소스의 실험용 변경 지점이 하나여야 합니다: {pattern} ({count}개)")
    return updated


def write(directory, name, value):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_bytes(value)


def stage_pi(repo, destination, role, c_file, python_file):
    revision = REVISIONS[role]
    native = git(repo, "show", revision + ":" + c_file).decode()
    bridge = git(repo, "show", revision + ":" + python_file).decode()
    if role == "main":
        native = '#include "pigpio.h"\n' + substitute(
            native, r'^        printf\("\[\*\] Connection Established\\n"\);',
            '        printf("[*] Connection Established\\n");\n        lab_tcp_connected();',
        )
        bridge = substitute(bridge, r"^from RPLCD\.i2c import CharLCD$", "from lcd import CharLCD")
        write(destination, "main/lcd.py", (HERE / "lcd.py").read_bytes())
        bridge = substitute(
            bridge, r'^    SERVER = MAIN_IP.*$', '    SERVER = __import__("os").environ["PILAB_SERVER_IP"]'
        )
        bridge = substitute(bridge, r'^    MAC_IP = "[^"]+"\n    MAIN_IP = "[^"]+"$', "")
    elif role == "light":
        native = '#include "pigpio.h"\n' + substitute(
            native, r"^void MOTOR_control\([^\n]+\{",
            "void MOTOR_control(int gpionum, int duty_cycle, int frequency, int duration) {\n    lab_arm_pwm();",
        )
        bridge = substitute(bridge, r'^pi_ip = "[^"]+"$', 'pi_ip = os.environ["PILAB_SERVER_IP"]')
        # The experiment owns its network namespace and starts a fresh process.
        bridge = substitute(bridge, r"^    kill_port\(port\)$", "")
        bridge = substitute(
            bridge, r"^                            light_cntr = str\(response_data\['lightControl'\]\)$",
            "                            light_cntr = str(response_data['lightControl'])\n"
            "                            open('/board/wss-ready', 'a').close()",
        )
    else:
        bridge = substitute(
            bridge, r'^JAVA_WS_SERVER_IP = "[^"]+"$',
            'JAVA_WS_SERVER_IP = __import__("os").environ["PILAB_SERVER_IP"]',
        )
    if role == "fire":
        native = substitute(native, r'inet_addr\("0\.0\.0\.0"\)', 'inet_addr("127.0.0.1")')
        native = substitute(
            native, r"^            buffer\[bytes_received\] = '\\0';",
            "            buffer[bytes_received] = '\\0';\n            lab_fire_frame(buffer, (unsigned)bytes_received);",
        )
    # A register-accurate DHT waveform is outside the first integration test.
    # Leave the callers and their cadence in place and record this boundary.
    if role == "fire":
        native = substitute(
            native, r"(?ms)^int read_dht11_dat\(int num\) \{.*?^}",
            'int read_dht11_dat(int num) {\n'
            '    Temperature[num] = 24;\n'
            '    gpioDelay(18000);\n'
            '    lab_sensor("legacy_dht", num);\n'
            '    return 1;\n}',
        )
    elif role == "air":
        native = substitute(
            native, r"(?ms)^int read_dht11_dat\(\)\s*\{.*?^}",
            'int read_dht11_dat() {\n'
            '    Temperature = 24;\n'
            '    Humidity = 42;\n'
            '    gpioDelay(18000);\n'
            '    lab_sensor("legacy_dht", 0);\n'
            '    return 1;\n}',
        )
    compile(bridge, f"{role}/bridge.py", "exec")
    write(destination, role + "/node.c", native)
    write(destination, role + "/bridge.py", bridge)


def stage_spring(repo, destination):
    revision = REVISIONS["spring"]
    listed = git(repo, "ls-tree", "-r", "--name-only", revision, SPRING_JAVA).decode().splitlines()
    for path in listed:
        if not path.endswith(".java") or path.endswith(("/FirebaseConfig.java", "/FirebaseService.java")):
            continue
        content = git(repo, "show", revision + ":" + path)
        if path.endswith("/IP.java"):
            text = content.decode()
            for name, role in {"Main": "MAIN", "Fire": "FIRE", "Light": "LIGHT", "Dust": "AIR"}.items():
                variable = name + "PiIp"
                text = substitute(
                    text, rf'^    public static final String {variable} = "[^"]+".*$',
                    f'    public static final String {variable} = System.getenv("PILAB_{role}_IP");',
                )
            content = text.encode()
        elif path.endswith("/WebSocketHandler.java"):
            content = substitute(
                content.decode(),
                r"^    public void sendDataByIp",
                "    public static java.util.List<String> labOpenPiIps() {\n"
                "        return java.util.List.copyOf(labOpenPiSessions().keySet());\n"
                "    }\n\n"
                "    public static Map<String, String> labOpenPiSessions() {\n"
                "        Map<String, String> sessions = new java.util.TreeMap<>();\n"
                "        CLIENTS.forEach((ip, session) -> {\n"
                "            if (session.isOpen()) sessions.put(ip, session.getId());\n"
                "        });\n"
                "        return sessions;\n"
                "    }\n\n"
                "    public void sendDataByIp",
            ).encode()
        write(destination, path, content)
    for path in ("settings.gradle", "gradlew", "gradle/wrapper/gradle-wrapper.jar"):
        write(destination, path, git(repo, "show", revision + ":" + path))
    (destination / "gradlew").chmod(0o755)
    gradle = git(repo, "show", revision + ":gradle/wrapper/gradle-wrapper.properties").decode()
    write(destination, "gradle/wrapper/gradle-wrapper.properties", gradle.rstrip() + "\n"
          + f"distributionSha256Sum={GRADLE_CHECKSUM}\n")

    build = git(repo, "show", revision + ":build.gradle").decode()
    build = substitute(build, r"^\s*implementation 'com\.google\.firebase:firebase-admin:[^']+'\n", "\n")
    write(destination, "build.gradle", build)
    write(destination, SPRING_JAVA + "fcm/FirebaseService.java", (HERE / "FirebaseService.java").read_bytes())
    write(destination, SPRING_JAVA + "handler/LabSessionProbe.java", (HERE / "LabSessionProbe.java").read_bytes())


def stage(repo, destination):
    for revision in REVISIONS.values():
        git(repo, "cat-file", "-e", revision + "^{commit}")
    for role, sources in PI_SOURCE.items():
        stage_pi(repo, destination / "pi", role, *sources)
    stage_spring(repo, destination / "spring")
    for name in ("node.py", "benchmark_workload.py", "requirements.txt"):
        shutil.copyfile(HERE / name, destination / "pi" / name)
    shutil.copytree(HERE / "hardware", destination / "pi/hardware")
    shutil.copyfile(HERE / "Dockerfile.pi", destination / "pi/Dockerfile")
    for name in ("Dockerfile.spring", "spring-entrypoint.sh"):
        target = "Dockerfile" if name.endswith(".spring") else name
        shutil.copyfile(HERE / name, destination / "spring" / target)
    write(destination, "legacy.json", json.dumps(REVISIONS, indent=2, sort_keys=True) + "\n")
