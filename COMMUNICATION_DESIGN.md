# 장치 간 통신

## 버튼이 조명을 바꾸는 경로

```text
MainPi GPIO → 입력 태스크 · 디바운스 → 버튼 런너블
    → 송신 큐 → POSIX 송신 스레드 → Spring /socket
    → 조명 명령 큐 → LightPi 수신 태스크 → 조명 런너블 → GPIO
    ← Spring이 LightPi의 적용 응답을 받아 큐 선두를 완료 처리
```

MainPi와 LightPi는 각자 Spring의 `/socket`에 연결합니다. MainPi의 입력 태스크는 디바운스로 눌림을 확정하면 순번을 붙여 고정 용량 링 버퍼에 넣습니다. 버퍼가 차면 거절한 순번을 로그에 남깁니다. 뮤텍스는 버퍼를 읽고 쓸 때만 잡습니다. 송신 스레드는 잠금을 푼 뒤 네트워크에 쓰므로, 전송이 지연돼도 입력 태스크는 다음 GPIO 에지를 처리할 수 있습니다.

Spring의 `ControlService`는 잠금 안에서 버튼을 접수하고 LED 목표와 큐를 갱신합니다. `LightCommandSender`는 조명 명령과 상태 조회를 단일 실행기에서 보냅니다. WebSocket에 쓸 때는 제어 상태의 잠금을 잡지 않습니다. LightPi는 수신 태스크에서 명령을 해석하고, 조명 런너블에서 GPIO에 쓴 값을 다시 읽어 확인합니다.

Spring은 설정된 LightPi 주소의 현재 WebSocket 세션에서 상태 보고와 적용 응답을 받습니다.

## 공통 WebSocket 송수신

네 Pi의 실행 파일은 공통 빌드 규칙으로 만든 `libpilab_websocket.a`와 libcurl을 링크합니다. 공통 모듈이 연결을 열고 소켓을 보관합니다. 한 번에 일부만 쓰면 남은 바이트를 이어서 보냅니다. 전송 시도의 기한 안에 텍스트 한 건을 다 쓰지 못하면 오류를 반환합니다. 연결을 닫으면 핸들과 소켓 참조를 비웁니다. [libcurl 송신 문서](https://curl.se/libcurl/c/curl_ws_send.html)

각 Pi의 네트워크 태스크 하나가 연결을 소유합니다. MainPi는 버튼을 JSON으로 만들고 대기 중 큐 알림과 소켓을 같이 감시합니다. LightPi는 텍스트를 받아 조명 프로토콜에서 해석하고, GPIO 상태 보고와 적용 응답을 같은 공통 전송 함수로 보냅니다. Pi별 수신 루프가 재접속 시작 시점을 정합니다.

`websocket_transport_wait()`는 한 번의 대기에서 감시한 소켓·종료·큐 알림을 함께 반환합니다. 각 태스크가 반환된 알림의 처리 순서를 정합니다. MainPi는 종료와 소켓 상태를 확인한 뒤 큐 알림을 읽습니다. LightPi는 수신 대기 시간이 끝날 때 heartbeat를 기록합니다.

LightPi는 `websocket_transport_receive_text()`로 텍스트 메시지를 읽습니다. 받은 조각을 버퍼에 붙이고, 메시지가 완성되면 프로토콜 처리부에 넘깁니다. 중간에 읽을 데이터가 없으면 LightPi 태스크가 지금까지 받은 바이트를 유지한 채 소켓을 다시 기다립니다. 새 연결에서는 빈 버퍼에서 시작합니다. [libcurl 수신 문서](https://curl.se/libcurl/c/curl_ws_recv.html)

MainPi는 버튼을 큐에서 꺼내기 전에 `websocket_transport_discard_pending()`으로 읽을 수 있는 프레임을 비우며 연결 종료를 확인합니다. 프레임 읽기와 종료 판별은 LightPi의 텍스트 수신과 같은 코드를 사용합니다. 종료가 확인되면 MainPi는 버튼을 큐에 둔 상태에서 재접속을 시작합니다.

## 메시지 계약

| 메시지 | 방향 | 주요 필드와 처리 의미 |
| --- | --- | --- |
| `ButtonPressed` | MainPi → Spring | `nodeId`, 실행별 `runId`, `payload.seq`로 누름 한 건을 식별합니다. 접수 결과는 Spring이 로그에 기록합니다. |
| `SensorSample` | 센서 Pi → Spring | `nodeId`, 실행별 `runId`, `payload.seq`, `payload.values`를 보냅니다. Spring이 받은 시각과 함께 처리한 샘플을 로그에 기록합니다. |
| `ServoCommand` | Spring → AirPi | `payload.commandId`에 UUID와 `payload.angle`에 목표 각도(정수 0~180°)를 보냅니다. |
| `ServoStateReported` | AirPi → Spring | 가상 서보가 있는 AirPi가 연결할 때마다 읽은 현재 각도를 `payload.angle`로 보냅니다. |
| `ServoCommandApplied` / `ServoCommandUnconfirmed` | AirPi → Spring | 같은 명령 ID와 각도를 돌려줍니다. 적용 응답은 가상 출력에 각도를 기록한 뒤에 보냅니다. |
| `FireAlarmCommand` | Spring → FirePi | `payload.commandId`에 UUID와 `payload.on`에 목표 경고등 상태를 불리언으로 보냅니다. |
| `FireAlarmStateReported` | FirePi → Spring | 가상 경고등이 있는 FirePi가 새 연결에서 읽은 상태를 `payload.on`으로 보냅니다. |
| `FireAlarmCommandApplied` / `FireAlarmCommandUnconfirmed` | FirePi → Spring | 같은 명령 ID와 `on` 값을 돌려줍니다. 가상 출력 확인까지 끝나면 적용 응답을 보냅니다. |
| `LightStateReported` | LightPi → Spring | 실행마다 생성한 최상위 `runId`와 연결 시 읽은 `payload.on`을 보고합니다. 같은 실행이 다시 연결하면 `runId`를 유지합니다. |
| `LightCommand` | Spring → LightPi | `payload.commandId`와 목표 상태 `payload.on`을 전달합니다. 명령 ID는 버튼 이벤트의 `nodeId/runId/seq`에서 만듭니다. |
| `LightCommandApplied` | LightPi → Spring | `payload.commandId`와 `payload.on`으로 해당 명령의 적용을 확인합니다. |
| `LightStateQuery` | Spring → LightPi | `payload.queryId`로 보류 중인 명령의 상태를 한 번 조회합니다. 이 경로에서는 명령 ID를 조회 ID로 사용합니다. |
| `LightStateSnapshot` | LightPi → Spring | `payload.queryId`, `payload.lastAppliedCommandId`, 조회 시점의 GPIO 값인 `payload.on`을 보고합니다. 적용 기록이 비어 있으면 마지막 ID는 `null`입니다. |

위 메시지는 `type`, `nodeId`, `payload`를 포함한 JSON 형식을 사용합니다. `ButtonPressed`와 `SensorSample`의 `runId`는 최상위 필드입니다. 센서 최신값은 `GET /home/data`로 조회할 수 있습니다.

Spring은 `airpi`, `firepi`, `lightpi`의 `SensorSample`을 각각 `PILAB_AIR_IP`, `PILAB_FIRE_IP`, `PILAB_LIGHT_IP`에 등록된 현재 연결에서 받습니다. AirQualityPi의 값은 `dust`, `temperature`(°C), `humidity`(0~100%)이고, FireDetectionPi의 값은 `infra`와 `gas`이고, LightPi의 조도값은 `light`입니다. `runId`는 소문자 16진수 32자이고 `seq`는 0보다 큰 부호 없는 64비트 정수입니다. 같은 장치 실행에서 마지막으로 처리한 순번 이하의 샘플은 최신값과 로그에 반영하지 않습니다. 값이 같아도 순번이 증가하면 새 샘플로 기록합니다.

수동 서보 시험에서는 한 번에 한 건만 보냅니다. Spring은 AirPi의 현재 연결에서 같은 ID와 각도의 응답을 받아야 적용 완료로 처리합니다. 연결이 닫히거나 3초 안에 답이 없으면 적용 미확인으로 기록합니다. PiLab은 Spring 컨테이너 안의 루프백 주소에서 수동 명령을 요청합니다.

서보 자동 모드에서 Spring은 같은 세션에서 받은 서보 상태와 먼지값으로 각도를 정합니다. 먼지가 100을 넘으면 0°, 그 이하면 90°를 요청합니다. 센서 수신 쪽에서 목표만 갱신하며 별도 송신 작업이 기존 명령 경로를 호출합니다. 응답 대기 중 목표가 여러 번 바뀌면 가장 최근 목표를 사용합니다. 적용이 미확인인 목표는 같은 값으로 반복 전송하지 않고 목표가 바뀌거나 새 세션에서 상태와 먼지값을 다시 받은 뒤 판단합니다.

FirePi 자동 경고등도 이 상태·명령 처리 방식을 공유합니다. `fire-room` 실험에서 Spring은 같은 FirePi 샘플에 들어 있는 적외선과 가스를 함께 읽어 원본의 가상 경보 조건을 적용합니다.

처리한 샘플마다 Spring은 `sensor_sample` 로그에 `nodeId`, `runId`, `seq`, `receivedAt`, `values`를 JSON으로 남깁니다. `receivedAt`은 Spring이 메시지를 해석한 직후의 UTC 시각입니다.

`PILAB_SENSOR_DB_PATH`를 지정하면 Spring이 SQLite의 `sensor_samples` 테이블에 기록합니다. `(node_id, run_id, seq)`가 기본 키입니다. 순번은 부호 없는 64비트 전체를 저장하고 정렬할 수 있도록 20자리 10진수 문자열로 표현합니다. 기존 순번 확인과 삽입은 한 SQL 문에서 수행합니다. 저장이 성공하면 메모리의 최신값을 갱신하고, 저장 오류는 `sensor_storage_failed` 로그와 WebSocket 오류로 보고합니다. `/home/data`는 Spring 재시작 뒤 새 샘플이 도착할 때부터 다시 갱신됩니다. DB 경로를 지정하지 않으면 순번과 최신값을 메모리에 둡니다.

AirQualityPi는 먼지값 하나 또는 `{"dust":23.5,"temperature":24.1,"humidity":42}`처럼 세 값을 함께 보냅니다. FireDetectionPi는 `{"infra":951,"gas":40}`을 보냅니다. `libpilab_telemetry.a`의 `telemetry_publish_samples()`는 센서 송신 프로세스마다 `runId`를 한 번 생성합니다. 각 `SensorSample`에는 장치 ID와 1부터 증가하는 순번을 붙입니다. 장치별 함수가 `--stdin`에서 읽은 한 줄을 `payload.values`로 변환합니다. 입력이 끝나면 연결을 닫습니다.

센서 스트림은 `websocket_transport_wait()`에서 입력과 WebSocket 파일 디스크립터를 함께 기다립니다. 입력은 줄바꿈 또는 EOF까지 버퍼에 모읍니다. 다음 샘플을 준비하는 동안 소켓 종료를 감지하면 공통 함수로 최대 세 번 재접속하고, 완성되지 않은 입력은 보관합니다. 이미 순번을 정한 샘플의 전송이 실패하면 `sensor_sample_abandoned`로 기록하고 복구 뒤 다음 입력을 처리합니다. 프로세스의 `runId`와 순번은 이어지고 재시도 한도에 이르면 오류로 종료합니다.

PiLab의 Compose 설정은 각 Pi에 Spring 주소표와 일치하는 IP를 부여합니다. 센서 송신 시험은 `/home/data`의 최신값, Spring 로그의 실행 ID·순번, 두 샘플을 받은 WebSocket 세션을 대조합니다. 기존 형식의 최상위 숫자 필드도 같은 최신값 조회에 반영됩니다.

### 가상 블라인드 방향 제어

Spring은 LightPi의 기존 장치 연결로 `BlindCommand`를 보냅니다. 페이로드는 Spring이 발급한 `commandId`와 `direction`(`UP` 또는 `DOWN`)을 담습니다. LightPi의 수신 태스크가 RTE를 통해 블라인드 SWC를 실행하고, 가상 모터 출력이 기록되면 같은 ID와 방향을 `BlindCommandApplied`로 응답합니다. 출력 쓰기에 실패하면 `BlindCommandUnconfirmed`를 반환합니다.

블라인드 채널은 장치당 한 건을 기다립니다. Spring은 현재 연결에서 온 응답의 명령 ID와 방향을 검증하며, 연결이 없거나 앞선 명령이 진행 중이면 새 요청을 거절합니다. 연결 종료나 응답 기한 초과는 미확인으로 반환합니다. 가상 출력 파일에는 마지막으로 적용한 방향을 남깁니다.

자동 모드의 Spring 제어기는 검증과 저장을 마친 LightPi 샘플을 받아, `IDLE`일 때 조도 `< 200`이면 `UP`, `> 750`이면 `DOWN`을 한 번 요청합니다. `200~750`에 들어오면 제어기만 `IDLE`로 돌아갑니다. 가상 출력 파일은 마지막으로 적용한 방향을 유지합니다. 앞 명령의 응답을 기다리는 동안 다음 방향은 한 건까지 보류하며, 중간 구간에 다시 들어오면 아직 전송하지 않은 보류 명령을 취소합니다. 전송이나 출력이 미확인이어도 같은 조도 구간에서 자동 재시도하지 않습니다.

## 명령 순서와 처리 완료

- Spring은 이미 접수한 버튼 이벤트 ID를 걸러냅니다. 대기열이 가득 차면 새 입력을 거절합니다. 접수된 입력은 순서대로 직전 목표를 반전해 명령을 만듭니다. 두 번 누르면 `켜기`와 `끄기`라는 별개의 절대 상태 명령이 생깁니다.
- Spring은 큐의 선두 명령 한 건만 확인 대기 상태에 둡니다. WebSocket 송신 함수가 반환한 뒤에도 큐 슬롯은 유지됩니다. 현재 LightPi 연결에서 같은 ID와 목표 상태가 담긴 적용 응답을 받으면 선두를 제거하고 다음 명령을 보냅니다.
- LightPi는 마지막으로 적용한 ID와 목표 상태를 메모리에 둡니다. 같은 명령을 다시 받으면 GPIO를 그대로 두고 응답만 보냅니다. Spring의 큐와 접수 기록은 상태 파일을 설정하면 재시작 후에도 복원됩니다.
- MainPi의 버튼 송신 경로는 Spring 접수 응답을 기다리지 않습니다. 버튼 송신 성공, Spring 접수, GPIO 적용은 관측 시 서로 다른 단계로 취급합니다.

## 응답 유실과 보류 복구

Spring에서 `pilab.control.light.ack-timeout-ms`에 양의 값을 주면, 송신 함수 반환 뒤부터 적용 응답을 기다립니다. 기본값 `0`은 타이머 비활성화입니다. 응답이 송신 함수 반환보다 먼저 도착해도 매칭할 수 있도록 대기 명령을 송신 전에 등록합니다.

첫 대기 시간이 지나면 같은 연결로 같은 ID와 목표 상태를 한 번 재전송합니다. 두 번째 대기 시간까지 응답이 없으면 큐를 보류하고 `ACK_TIMEOUT`과 `application=UNKNOWN`을 기록합니다. LightPi가 이미 GPIO를 바꿨을 수 있습니다. Spring은 적용 상태를 확인할 때까지 큐의 다음 명령을 보내지 않습니다.

PiLab에서 조회를 요청하면 Spring은 같은 연결로 `LightStateQuery`를 한 번 보냅니다. LightPi는 마지막으로 적용한 명령 ID와 **새로 읽은 GPIO 값**을 답합니다. Spring은 현재 연결과 조회 ID를 확인합니다. 마지막 적용 ID와 GPIO 값이 모두 큐 맨 앞의 명령과 일치하면 그 명령을 완료 처리합니다. 응답이 없거나 값이 다르면 큐는 보류된 채로 남습니다.

## WebSocket 재연결

MainPi의 초기 연결·재접속과 LightPi의 재접속은 공통 `websocket_transport_open_with_retry()`에서 시도 간격과 최대 횟수를 공유합니다. 두 Pi는 시도와 실패를 기록할 함수를 넘깁니다. LightPi의 첫 연결은 한 번 시도하며, 재접속에 성공한 후의 GPIO 상태 보고는 LightPi 태스크가 맡습니다.

MainPi의 송신 스레드는 시작 시 Spring에 연결합니다. `poll`로 WebSocket, 큐 알림, 종료 알림을 함께 기다리기 때문에 버튼 입력 없이도 연결 종료를 감지할 수 있습니다. 첫 연결과 이후 재연결은 각각 최대 세 번 시도하며 대기 시간은 0초, 1초, 2초입니다. 재접속 중 접수된 버튼 이벤트는 고정 용량 큐에 남습니다. 이미 송신 중 오류가 난 이벤트는 전달 여부가 불명확하다고 기록하고, 이후 이벤트는 새 연결에서 처리합니다.

LightPi가 수신 중 연결 종료를 감지하면 기존 소켓을 정리하고 새 WebSocket 연결을 최대 세 번 시도합니다. 첫 시도는 즉시 하고, 두 번째와 세 번째 시도 전에는 각각 1초와 2초를 기다립니다. 세 시도가 모두 실패하면 LightPi는 한도 도달을 기록하고 오류로 종료합니다. GPIO 요청과 마지막 적용 기록은 연결을 다시 시도하는 동안 유지합니다. 새 세션에서는 GPIO를 읽어서 다시 `LightStateReported`를 보냅니다. libcurl이 종료된 연결에 반환하는 `CURLE_GOT_NOTHING`도 이 경로에서 처리합니다. [libcurl WebSocket 수신 문서](https://curl.se/libcurl/c/curl_ws_recv.html)

미확인 선두 명령이 없으면 Spring은 이전 세션의 종료와 마지막 확인 상태에 일치하는 GPIO 보고를 확인합니다. 아직 송신을 시작하지 않은 대기 명령은 새 세션에서 이어서 처리합니다. 이전 세션의 종료 알림이 늦게 도착해도 세션 객체를 비교해 새 연결을 유지합니다.

이미 송신을 시작한 선두 명령이 미확인 상태면 Spring은 새 세션의 시작 보고를 받고 그 명령의 ID로 상태를 한 번 조회합니다. 조회 결과의 마지막 적용 ID와 GPIO 값이 선두 명령과 일치하면 완료 처리하고 다음 명령을 보냅니다.

조회 결과가 선두 명령을 확인해 주지 않으면, Spring은 보내던 때와 같은 LightPi 실행인지 확인합니다. 같은 실행이고 마지막 적용 ID와 GPIO 값이 Spring이 직전에 확인한 기록과 일치하면, 같은 명령 ID와 목표 값을 재연결 전체에서 한 번 재전송합니다. 그 적용 응답을 받은 뒤 다음 명령을 보냅니다. 실행 ID가 바뀌었거나, 조회 응답이 없거나, 확인 기록과 다르면 큐를 보류합니다.

## Spring 재시작과 큐 복원

`pilab.control.light.state-file`을 설정하면 Spring은 큐 순서와 목표, 접수한 입력 ID, 송신 준비를 마친 선두와 대상 LightPi 실행 ID, 마지막 확인 상태, 복구 재전송 사용 여부를 한 파일에 저장합니다.

| 시점 | 먼저 저장하는 것 |
| --- | --- |
| 버튼을 접수할 때 | 새 명령과 접수 ID를 저장한 뒤 접수 성공을 기록합니다. |
| 선두를 송신할 때 | 이미 보냈을 수 있는 명령으로 표시한 뒤 WebSocket 쓰기를 실행합니다. |
| 선두의 적용을 확인할 때 | 선두를 큐에서 제외하고 확인 상태를 저장한 뒤 다음 명령을 보냅니다. |

Spring이 송신 표시를 저장한 직후 내려가면, 다시 올라온 Spring은 그 명령이 실제로 전송됐는지 알 수 없습니다. 같은 LightPi에서 마지막 적용 ID와 GPIO 값을 조회해 확인합니다. 이전에 확인한 상태와 일치하면 같은 ID로 한 번 더 보내는 규칙을 적용합니다. LightPi도 재시작해 적용 ID가 사라지는 등 상태를 확인할 수 없는 경우에는 명령을 보류합니다.

상태 파일은 단일 Spring 프로세스가 소유하는 로컬 파일입니다. 같은 디렉터리에 임시 파일을 쓰고 동기화한 뒤 이름을 원자적으로 교체하고 디렉터리를 동기화합니다. 저장 오류가 발생하면 로그에 기록하고 해당 프로세스에서 조명 큐 변경을 중단합니다. 시작할 때 손상된 파일을 만나면 Spring 시작을 실패 처리합니다. 큐를 잠근 채 파일을 저장하며, 접수한 ID 전체를 매번 기록합니다. 메모리 큐와 파일 큐의 지연은 [벤치마크](BENCHMARK_SPEC.md#mainpi-입력--lightpi-출력)에 있습니다.

## 조명 장애 재현

저장소 루트에서 아래 명령을 실행합니다. `summary.json`에서 적용한 명령 ID와 가상 GPIO 전이, 마지막 큐 상태를 확인할 수 있습니다.

| 명령 | 통과 조건 |
| --- | --- |
| `./pilab lab light-ack-delay` | 첫 LED 적용 응답을 보류해도 둘째 버튼을 접수합니다. 첫 응답 뒤에 둘째 명령을 보냅니다. |
| `./pilab lab light-ack-retry --drop-count 1 --timeout-ms 1500` | 적용 응답을 한 번 버립니다. 같은 ID를 다시 받아도 LED는 한 번만 바뀌고 다음 명령을 처리합니다. |
| `./pilab lab light-ack-retry --drop-count 2 --timeout-ms 1500` | 응답을 두 번 버립니다. 재전송은 한 번이며 큐는 보류됩니다. `--recover`를 붙이면 상태 조회로 큐를 풀 수 있는지 확인합니다. |
| `./pilab lab light-reconnect` | 큐가 빈 상태에서 연결을 닫습니다. 같은 LightPi 프로세스가 새 세션에서 다음 버튼을 적용합니다. |
| `./pilab lab light-reconnect --pending` | 첫 명령은 적용됐고 응답만 유실됐습니다. 다시 연결한 뒤 첫 적용을 조회로 확인하고 둘째 명령만 보냅니다. |
| `./pilab lab light-reconnect --undelivered` | 첫 명령은 전송하지 않은 상태입니다. 같은 LightPi가 돌아오면 동일 ID로 한 번 보내고, 응답 뒤에 둘째 명령을 보냅니다. |
| `./pilab lab light-reconnect --restart` | 명령 유실 뒤 LightPi도 재시작합니다. 새 실행 ID라면 두 명령을 보류하고 재전송하지 않습니다. |
| `./pilab lab light-reconnect --spring-restart` | 빈 큐에서 Spring만 재시작합니다. 같은 LightPi가 새 세션을 열고 다음 버튼을 적용합니다. |
| `./pilab lab light-reconnect --spring-restart-pending` | 첫 적용 응답을 버리고 둘째 명령이 대기할 때 Spring만 재시작합니다. 파일의 큐를 복원한 뒤 첫 명령을 조회하고 둘째 명령을 적용합니다. |
| `./pilab lab light-reconnect --reject-attempts 2` | 첫 두 연결 요청을 HTTP 503으로 거절합니다. 세 번째 시도에서 연결하면 다음 버튼을 적용합니다. |
| `./pilab lab light-reconnect --reject-attempts 3` | 세 요청 모두 거절하면 LightPi가 오류로 종료합니다. |

`1,500 ms`는 응답 유실 실험에 전달한 값입니다. `manifest.json`에서 적용한 설정을, `services.log`에서 Spring의 접수·송신과 LightPi의 출력을 확인할 수 있습니다.
