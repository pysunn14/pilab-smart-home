# 장치 실행과 관측

## Spring과 C 실행 파일 빌드

Spring은 JDK 17로 빌드합니다. 저장소 루트에서 다음 명령을 실행합니다.

```sh
(cd spring && ./gradlew test bootJar)
docker build -t pilab-spring ./spring
```

Pi 실행 파일을 직접 빌드하려면 Linux에 C 컴파일러와 Make, `pkg-config`를 설치합니다. 장치별로 다음 개발 라이브러리가 필요합니다. libcurl은 WebSocket을 지원하는 빌드를 사용합니다.

| 장치 | 개발 라이브러리 | 빌드 |
| --- | --- | --- |
| MainPi | libgpiod 2, libcurl 7.86 이상 | `make -C mainpi button-monitor` |
| LightPi | libgpiod 2, libcurl 8.14 이상, json-c 0.15 이상 | `make -C lightpi light-node` |
| AirPi | libcurl 7.86 이상, json-c 0.15 이상 | `make -C airpi air-node` |
| FirePi | libcurl 7.86 이상, json-c 0.15 이상 | `make -C firepi fire-node` |

네 실행 파일이 `libpilab_websocket.a`를 링크합니다. 이 라이브러리만 빌드하려면 `make -C common websocket`을 실행합니다. 헤더는 [common/bsw/services/websocket_transport.h](../common/bsw/services/websocket_transport.h)에 있습니다.

`WS_URL`에는 Spring의 `/socket` 주소를, `GPIOCHIP`과 `GPIO_OFFSET`에는 보드의 칩 경로와 MainPi 버튼의 라인 오프셋을 지정합니다. MainPi 명령의 `10`은 디바운스 시간(ms), LightPi 명령의 `18`과 `off`는 LED 라인 오프셋과 초기 상태입니다.

```sh
mainpi/build/button-monitor "$GPIOCHIP" "$GPIO_OFFSET" 10 "$WS_URL"
lightpi/build/light-node "$GPIOCHIP" 18 off "$WS_URL"
```

Spring의 `PILAB_LIGHT_IP`, `PILAB_AIR_IP`, `PILAB_FIRE_IP`에는 서버에서 보이는 각 장치의 접속 IP를 설정합니다. 조명 큐를 켜려면 Spring에 양의 값으로 `--pilab.control.light.queue-capacity`를 전달합니다.

## 센서값 입력

AirPi는 먼지값 한 개 또는 먼지·온도(°C)·상대습도(%) 세 개를 한 줄로 받습니다. FirePi 입력 한 줄에는 적외선과 가스 값이 들어갑니다.

```sh
printf '23.5\n41.2 24.1 42\n' | airpi/build/air-node "$WS_URL" --stdin
printf '951 40\n812 12\n' | firepi/build/fire-node "$WS_URL" --stdin
```

Docker에서 개별 장치가 Spring과 통신하는지 확인할 수 있습니다.

| 명령 | 확인하는 동작 |
| --- | --- |
| `./pilab lab air-dust-smoke` | Spring이 먼지 샘플 두 건을 수신하고 SQLite에 남깁니다. |
| `./pilab lab air-servo-smoke --angle 0` | AirPi가 서보 목표 각도 `0°`를 가상 출력 파일에 쓰고 명령 ID를 응답합니다. |
| `./pilab lab air-auto-servo-smoke` | 먼지를 `80 → 120 → 80`으로 바꾸면 서보가 `90° → 0° → 90°`로 바뀝니다. |
| `./pilab lab fire-sensor-smoke` | 적외선과 가스를 같은 샘플에 담아 Spring에 보냅니다. |
| `./pilab lab fire-alarm-smoke` | FirePi가 센서 연결을 유지하면서 경고등 켜기와 끄기를 적용합니다. |

AirPi에 가상 서보 파일을 붙이려면 `--room-file PATH` 뒤에 `--servo-file PATH`를 지정합니다. Spring의 자동 제어는 `PILAB_AIR_AUTO_SERVO=true`로 켭니다.

### AirPi가 파일을 읽게 하기

통합 작업대와 별도로 AirPi 하나를 띄울 때 사용할 수 있습니다. 첫 번째 터미널에서 새 디렉터리에 가상 센서 파일을 열어 둡니다.

```sh
ROOM_DIR="$(mktemp -d)"
printf '%s\n' "$ROOM_DIR"
./pilab web --room-file "$ROOM_DIR/air.txt"
```

두 번째 터미널에서도 `ROOM_DIR`을 위에서 출력한 경로로 지정하고 실행합니다.

```sh
docker compose -f lab/compose.observability.yaml up -d --build --wait spring
docker compose -f lab/compose.observability.yaml run --build --rm -T \
  --volume "$ROOM_DIR:/room:ro" air --room-file /room/air.txt
```

`./pilab lab environment set --dust 80 --temperature 25 --humidity 45` 또는 웹의 **값 적용**으로 파일의 내용을 바꿉니다. `get`은 현재 값을 조회하고, `sample`은 AirPi 표준 입력에 전달할 수 있는 한 줄을 출력합니다.

PiLab은 값을 바꿀 때 파일 전체를 교체합니다. AirPi는 시작 3초 뒤부터 단조 시계 기준으로 3초마다 파일을 읽습니다. 같은 값도 새 샘플로 보냅니다. 수집 스레드는 전송을 기다리지 않으며, 전송 대기 칸에는 최신 한 건만 남깁니다. 파일 읽기가 실패한 주기는 로그에 기록합니다.

### FirePi와 LightPi 화면

`./pilab lab fire-room`은 기본 포트 `8792`에서 열립니다. 적외선이 `800` 이하이거나 가스가 `400` 이상이면 가상 경고등이 켜집니다. 두 조건이 모두 해제되면 꺼집니다. 장치 로그의 `fire_alarm_applied` 또는 결과 폴더의 `virtual-alarm/alarm.txt`에서 확인할 수 있습니다.

`./pilab lab light-room`은 기본 포트 `8794`에서 열립니다. 화면에서 조도를 바꾸거나 **올리기**와 **내리기**를 누릅니다. CLI에서는 아래 명령으로 조작합니다.

```sh
./pilab lab environment set --node fire --infra 812 --gas 40 --url http://127.0.0.1:8792
./pilab lab environment set --node light --light 180 --url http://127.0.0.1:8794
./pilab lab blind up
./pilab lab blind down
```

`./pilab lab light-room --auto-blind`를 실행하면 조도로 방향을 정합니다. `500 → 180 → 800 → 500 → 800`을 차례로 넣으면 `180`에서 한 번 올라가고 마지막 `800`에서 한 번 내려갑니다. 조도가 중간 구간으로 돌아와야 다음 방향을 요청합니다. 자동 모드에서는 수동 명령이 잠깁니다. 결과 폴더의 `virtual-blind/direction.txt`에서 `0`은 초기 상태, `1`은 올리기, `-1`은 내리기입니다.

## SQLite와 Grafana

Spring의 `PILAB_SENSOR_DB_PATH`에 쓰기 가능한 경로를 주면 센서 샘플을 SQLite에 저장합니다. `GET /home/data`는 메모리에 있는 최신값을 반환합니다. 아래 Compose 구성은 DB 디렉터리를 볼륨에 두고 Prometheus와 Grafana도 띄웁니다.

```sh
docker compose -f lab/compose.observability.yaml up -d --build --wait
docker compose -f lab/compose.observability.yaml build air fire
{ printf '23.5\n'; sleep 6; printf '41.2\n'; } | docker compose -f lab/compose.observability.yaml run --rm -T air --stdin
{ printf '951 40\n'; sleep 6; printf '812 12\n'; } | docker compose -f lab/compose.observability.yaml run --rm -T fire --stdin
```

<http://127.0.0.1:13000/d/pilab-sqlite>에서 센서별 연결 상태, 마지막 정상 수신 이후 경과 시간, SQLite 저장 결과를 볼 수 있습니다. Prometheus가 Spring의 `/actuator/prometheus`를 5초마다 읽습니다. 저장 p95·p99는 성공한 SQLite 저장의 히스토그램으로 구한 근사치입니다. 순번 검사와 커밋까지 재며, WebSocket 전송 및 SQLite 호출 전 잠금 대기는 제외합니다.

포트나 서브넷이 겹치면 `PILAB_GRAFANA_PORT`나 `PILAB_PREFIX`를 지정합니다. 다음 명령으로 컨테이너를 내릴 수 있습니다. SQLite와 Prometheus 데이터는 Compose 볼륨에 남습니다.

```sh
docker compose -f lab/compose.observability.yaml down
```

센서 명령 `air-dust-smoke`와 `fire-sensor-smoke`의 `--idle-reconnect`는 샘플 사이에 WebSocket을 닫았다가 같은 실행 ID로 다음 순번이 저장되는지 확인합니다. `--spring-restart`는 SQLite 볼륨을 유지한 채 Spring을 재시작합니다. `--hold-down-until-exhausted`를 같이 쓰면 세 번의 재접속이 실패한 뒤 Spring을 올립니다. 두 경우 모두 기존 SQLite 행을 확인합니다.

## 버튼 경로만 확인하기

Docker 없이 화면만 둘러보려면 `./pilab web`으로 <http://127.0.0.1:8791/>을 열고 **예시 재생**을 선택합니다. 화면의 상태·시각·센서값은 브라우저가 생성합니다.

`./pilab web --control --spring-receiver`를 실행하고 **실험 시작**을 누릅니다. 이 실험은 MainPi와 Spring을 띄웁니다. GPIO 20을 누르면 Spring이 받은 `ButtonPressed` 순번이 화면과 `events.jsonl`에 남습니다.

Linux에서 `lab/requirements.txt`를 설치하면 보드 없이 아래 테스트를 실행할 수 있습니다. MainPi 테스트는 파이프로 버튼 입력을 넣고 로컬 WebSocket 서버에서 순번을 확인합니다. JDK 17을 설치한 환경에서는 `PILAB_TEST_SPRING=1`을 붙여 Spring을 포함할 수 있습니다.

```sh
python3 -m unittest discover -s tests -p test_button_monitor.py -v
python3 -m unittest discover -s tests -p test_light_node.py -v
```
