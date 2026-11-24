# PiLab

라즈베리파이 네 대가 Spring 서버에 연결되는 스마트홈입니다. Docker로 각 Pi의 C 프로그램을 띄우고, 웹에서 가상 버튼과 센서를 조작할 수 있습니다.

## 실행

Git으로 복제한 저장소에서 실행합니다. Python 3.9 이상과 실행 중인 Docker, Docker Compose v2가 필요합니다.

```sh
./pilab web --room
```

<http://127.0.0.1:8795/room.html>에서 **실험 시작**을 누릅니다. 첫 실행은 Docker 이미지를 빌드합니다. Spring과 네 Pi가 연결되면 다음 입력을 하나씩 바꿔 보세요.

| 입력 | 확인할 출력 |
| --- | --- |
| MainPi에서 **버튼 누르기** | LightPi의 가상 LED가 반전됩니다. 화면 아래에서 MainPi 입력, Spring 수신, LightPi 적용 여부를 볼 수 있습니다. |
| AirPi의 먼지를 `120`으로 설정 | 다음 센서 읽기 뒤 가상 서보가 `0°`로 바뀝니다. AirPi는 3초마다 읽습니다. |
| FirePi의 적외선 값을 `750`으로 설정 | 가상 경고등이 켜집니다. |
| LightPi 조도를 `180`으로 설정 | 가상 블라인드 방향이 올리기로 바뀝니다. |

**실험 정지**를 누르면 PiLab이 컨테이너를 내립니다. 기본 결과 경로인 `~/.local/state/pilab/runs/`에 `summary.json`, 장치 로그, 버튼 기록과 SQLite 센서 이력이 남습니다. 포트가 겹치면 `--port`로, Docker 서브넷이 겹치면 `--subnet-prefix`로 변경합니다.

웹에서 시작한 실험에 터미널로도 입력을 보낼 수 있습니다.

```sh
./pilab lab control button --url http://127.0.0.1:8795
./pilab lab environment set --node air --dust 120 --url http://127.0.0.1:8795
```

## 실행 구조

Compose는 Spring과 네 Pi를 각각 컨테이너로 띄웁니다. 각 Pi는 격리된 Docker 네트워크에서 Spring의 `/socket`에 WebSocket을 엽니다. 조명과 센서 제어 메시지도 그 연결로 돌아옵니다.

브라우저는 호스트에서 실행한 PiLab 웹 서버에 접속합니다. PiLab이 가상 GPIO에 버튼 입력을 넣고, 센서값은 파일 또는 장치의 표준 입력으로 전달합니다. 화면은 Spring의 최신 센서값과 각 장치가 기록한 가상 출력을 읽습니다.

## 테스트

```sh
./pilab lab integrated-smoke
```

네 Pi를 연결하고 버튼을 한 번 누릅니다. LightPi가 LED를 바꾸면 테스트가 적용 응답을 보류합니다. 그 사이 두 센서의 다음 샘플이 SQLite에 저장되는지 확인합니다. 응답을 보낸 뒤 큐가 비고, LED 전이가 한 번만 기록돼야 통과합니다.

- [장치 실행과 관측](docs/device-setup.md)에서 개별 장치 빌드, 센서 입력, Grafana 실행법을 볼 수 있습니다.
- [장치 간 통신](COMMUNICATION_DESIGN.md)에 메시지 필드, 조명 명령 큐, 재연결 시나리오를 정리했습니다.
- [벤치마크](BENCHMARK_SPEC.md)에서 버튼 수신 지연과 메모리·파일 큐 비교를 실행할 수 있습니다.
