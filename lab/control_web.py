"""Serve the dashboard and a small, same-origin control API."""

import json
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .room_environment import RoomEnvironment
from .session import SessionConflict


API_PATH = "/api/lab"
BLIND_PATH = API_PATH + "/blind"
BLIND_DIRECTIONS = ("UP", "DOWN")
ENVIRONMENT_PATHS = {"air": "/api/environment", "fire": "/api/environment/fire", "light": "/api/environment/light"}
ACTIONS = {"start", "button", "stop"}
IDENTITY_HEADER = "Tailscale-User-Login"


def allowed_request(headers, port, external_origin=None, operator=None):
    hosts = headers.get_all("Host", [])
    if len(hosts) != 1:
        return None
    host = hosts[0]
    if (
        external_origin is not None and operator
        and host == urlsplit(external_origin).netloc
        and headers.get_all(IDENTITY_HEADER, []) == [operator]
    ):
        return external_origin
    forwarded = any(name in headers for name in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", IDENTITY_HEADER))
    if not forwarded and host in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return "http://" + host
    return None


def dashboard_handler(directory, session=None, external_origin=None, operator=None, *, room_file=None,
                      room_overrides=None, move_blind=None, auto_blind=False, room_provider=None):
    overrides = room_overrides or {}
    nodes = {path: node for node, path in ENVIRONMENT_PATHS.items()}
    rooms = {} if room_provider else {
        node: overrides[node] if node in overrides else RoomEnvironment(room_file if node == "air" else None, node=node)
        for node in ENVIRONMENT_PATHS
    }

    class DashboardHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

        def send_json(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def control_origin(self):
            return allowed_request(self.headers, self.server.server_port, external_origin, operator)

        def sensor_room(self):
            node = nodes[self.path]
            room = room_provider(node) if room_provider else rooms[node]
            if room is None:
                self.send_json(409, {"error": "통합 작업대를 시작한 뒤 조작할 수 있습니다."})
            return room

        def do_GET(self):
            if room_provider and self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/room.html")
                self.end_headers()
                return
            if self.path in nodes:
                if self.control_origin() is None:
                    self.send_json(403, {"error": "이 주소에는 가상 방 조회 권한이 없습니다."})
                else:
                    room = self.sensor_room()
                    if room is not None:
                        self.send_json(200, room.snapshot())
                return
            if self.path == BLIND_PATH:
                allowed = self.control_origin() is not None
                mode = {"enabled": move_blind is not None and allowed}
                if auto_blind and allowed:
                    mode["mode"] = "automatic"
                self.send_json(200, mode)
                return
            if self.path != API_PATH:
                return super().do_GET()
            if session is None or self.control_origin() is None:
                self.send_json(200, {"enabled": False})
                return
            self.send_json(200, {"enabled": True, **session.snapshot()})

        def do_POST(self):
            if self.path in nodes:
                self.update_environment()
                return
            action = self.path.removeprefix(API_PATH + "/")
            direction = self.path.removeprefix(BLIND_PATH + "/")
            blind = direction in BLIND_DIRECTIONS and self.path == f"{BLIND_PATH}/{direction}"
            if not blind and (action not in ACTIONS or self.path != f"{API_PATH}/{action}"):
                self.send_json(404, {"error": "알 수 없는 조작입니다."})
                return
            origin = self.control_origin()
            available = (move_blind is not None or auto_blind) if blind else session is not None
            if not available or origin is None or self.headers.get_all("Origin", []) != [origin]:
                self.send_json(403, {"error": "이 주소에는 실험 제어 권한이 없습니다."})
                return
            if (self.headers.get_all("Content-Length", []) != ["2"] or "Transfer-Encoding" in self.headers
                    or self.headers.get_content_type() != "application/json"):
                self.send_json(400, {"error": "실험 요청 형식이 올바르지 않습니다."})
                return
            self.connection.settimeout(3)
            try:
                body = self.rfile.read(2)
            except TimeoutError:
                body = b""
            if body != b"{}":
                self.send_json(400, {"error": "실험 요청 형식이 올바르지 않습니다."})
                return
            if blind:
                if auto_blind:
                    self.send_json(409, {"error": "블라인드는 조도에 따라 자동 제어 중입니다."})
                    return
                try:
                    status, receipt = move_blind(direction)
                except (RuntimeError, ValueError) as error:
                    self.log_error("블라인드 명령 전달 실패. %s", error)
                    self.send_json(502, {"error": "Spring에서 블라인드 응답을 받지 못했습니다."})
                    return
                self.send_json(status, receipt)
                return
            try:
                session.request(action)
            except SessionConflict as error:
                self.send_json(409, {"error": str(error)})
                return
            self.send_json(202, {"enabled": True, **session.snapshot()})

        def update_environment(self):
            origin = self.control_origin()
            if origin is None or self.headers.get_all("Origin", []) != [origin]:
                self.send_json(403, {"error": "이 주소에는 가상 방 제어 권한이 없습니다."})
                return
            lengths = self.headers.get_all("Content-Length", [])
            if (len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit()
                    or len(lengths[0]) > 4 or not 1 <= int(lengths[0]) <= 4096
                    or "Transfer-Encoding" in self.headers or self.headers.get_content_type() != "application/json"):
                self.send_json(400, {"error": "가상 방 요청 형식이 올바르지 않습니다."})
                return
            self.connection.settimeout(3)
            length = int(lengths[0])
            try:
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError
                patch = json.loads(body)
            except (ValueError, TimeoutError, RecursionError):
                self.send_json(400, {"error": "가상 방 요청을 읽을 수 없습니다."})
                return
            room = self.sensor_room()
            if room is None:
                return
            try:
                snapshot = room.update(patch)
            except OSError as error:
                self.log_error("가상 센서 파일을 갱신하지 못했습니다. %s", error)
                self.send_json(500, {"error": "가상 센서 파일을 갱신하지 못했습니다."})
                return
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
                return
            self.send_json(200, snapshot)

    return DashboardHandler


def call_control(base_url, action):
    path = API_PATH if action == "status" else API_PATH + "/" + action
    if action == "status":
        request = Request(base_url + path)
    else:
        request = Request(base_url + path, data=b"{}", headers={
            "Content-Type": "application/json", "Origin": base_url,
        })
    timeout = 8 if path.startswith(BLIND_PATH + "/") else 4
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def call_environment(base_url, patch=None, *, node="air"):
    request = Request(base_url + ENVIRONMENT_PATHS[node])
    if patch is not None:
        request.data = json.dumps(patch, allow_nan=False).encode()
        request.add_header("Content-Type", "application/json")
        request.add_header("Origin", base_url)
    with urlopen(request, timeout=4) as response:
        return json.load(response)
