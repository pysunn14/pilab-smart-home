import threading
import asyncio
import os
import time
from RPLCD.i2c import CharLCD
import socket
import json
import random
import websockets
import ssl

random.seed(time.time())

class Building:
    def __init__(self):
        self.data = {
            "temperature": 25.0,
            "humidity": 40.0,
            "gas": 0.0,
            "light": 1.0,
            "dust": 0.65,
        }
        self.control = {
            "buttonSignal": 0,
        }

class SocketServer(threading.Thread):
    def __init__(self, building, host='127.0.0.1', port=8080):
        super().__init__()
        self.host = host
        self.port = port
        self.server_socket = None
        self.building = building

    def run(self):
        self.start_server()

    def start_server(self):
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        
        while True:
            print("[Socket] Waiting for connection...")
            try:    
                client_socket, client_address = self.server_socket.accept()
                print(f"[Socket] Connection from {client_address}")
        
            except Exception:
                continue
            
            while True:    
                data = client_socket.recv(1024).decode()
                if data:
                    # button 눌림 상태 설정
                    self.building.control['buttonSignal'] = data
                else:
                    break
                
class WebSocketClient(threading.Thread):
    
    def __init__(self, uri, building):
        super().__init__()
        self.uri = uri
        self.building = building
        self.websocket = None
        self.prev = 0
        self.loop = None  # 이벤트 루프를 저장할 변수 추가
        
    async def communicate(self):
        while True:
            print("[Web] Websocket 연결 시도 ...")
                
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            try:
                async with websockets.connect(self.uri, ssl=ssl_context, ping_interval=1 ,ping_timeout=5) as websocket:
                    self.websocket = websocket
                    print("[Web] 연결 성공!")
                    
                    
                    await asyncio.gather(
                        self.send_data(),
                        self.receive_data()
                    )
                    
            except websockets.ConnectionClosedError as e:
                print(f"[Web] 웹소켓 연결 해제되어 1초 뒤 연결 재시도합니다 : {e}")
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"[Web] 예외 발생: {e}. 1초 뒤 재연결 시도합니다.")
                await asyncio.sleep(1)
                
    async def send_data(self):
        while True:
            try:
                if self.websocket:
                    await self.websocket.send(json.dumps(self.building.control))
                    print("[Web] Sent data:", self.building.control)
            except Exception as e:
                print(f"[Web] 데이터 전송중 오류 발생 {e}")
                await asyncio.sleep(1)
            await asyncio.sleep(1)
            
    async def receive_data(self):
        while True:
            try:
                if self.websocket:
                    await self.process_response()
                    
            except websockets.ConnectionClosed as e:
                print(f"[Web] 연결 종료됨: {e}. 연결을 재시도합니다..")
                raise
            
            except Exception as e:
                print(f"[Web] 데이터를 받는중 오류가 발생했습니다 : {e}")
                raise
        

    async def process_response(self):
        try:
            response = await asyncio.wait_for(self.websocket.recv(), timeout = 5)
            response_data = json.loads(response)
            
            # 데이터 업데이트
            for key, value in response_data.items():
                print(f"[Web] [{key} : {value}]")
                self.building.data[key] = value
                
        except json.JSONDecodeError:
                print("[Web] JSON Decode Error in response")
                
        except (asyncio.TimeoutError, websockets.ConnectionClosedError) as e:
                raise websockets.ConnectionClosedError("타임아웃으로 연결 종료합니다")
            
        except Exception:
            print("예기치 않은 오류 발생")

    def run(self):
        self.loop = asyncio.new_event_loop()  # 새 이벤트 루프 생성
        asyncio.set_event_loop(self.loop)    # 해당 스레드에 이벤트 루프 설정₩
        self.loop.run_until_complete(self.communicate())  # 이벤트 루프 실행

class LCDController(threading.Thread):
    def __init__(self, building, address=0x27, cols=16, rows=2):
        super().__init__()
        self.building = building
        self.lcd = CharLCD(i2c_expander='PCF8574', address=address, port=1, cols=cols, rows=rows)

    def display_data(self):
        try:
            while True:
                for key, value in self.building.data.items():
                    # self.lcd.write_string("hello world")
                    self.lcd.write_string(f"{key}: {value}")
                    time.sleep(2)
                    self.lcd.clear()
                    time.sleep(0.1)
        finally:
            self.lcd.clear()

    def run(self):
        self.display_data() 


# Main
if __name__ == "__main__":
    
    PORT = 8080
    SERVER = os.environ.get("PILAB_SERVER_IP")
    if not SERVER:
        raise SystemExit("Set PILAB_SERVER_IP to the Spring server address.")

    building = Building()

    socket_server_thread = SocketServer(host="127.0.0.1", port=8000, building=building)
    websocket_client_thread = WebSocketClient(uri=f"wss://{SERVER}:{PORT}/socket", building=building)
    lcd_controller_thread = LCDController(building=building)

    # 스레드 시작
    socket_server_thread.start()
    websocket_client_thread.start()
    lcd_controller_thread.start()

    # 스레드가 종료 대기
    socket_server_thread.join()
    websocket_client_thread.join()
    lcd_controller_thread.join()
    
    