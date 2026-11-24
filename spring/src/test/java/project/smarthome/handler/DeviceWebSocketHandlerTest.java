package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import project.smarthome.telemetry.SensorIngestion;
import project.smarthome.telemetry.SensorSnapshot;

import java.net.InetSocketAddress;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DeviceWebSocketHandlerTest {
    private final DeviceSessions sessions = new DeviceSessions();
    private final SensorSnapshot sensors = new SensorSnapshot();
    private final ObjectMapper mapper = new ObjectMapper();
    private final AirServoCommands servo = new AirServoCommands(mapper, sessions, "");
    private final AirDustControl airDust = new AirDustControl(servo, sessions, "", false);
    private final FireAlarmCommands alarm = new FireAlarmCommands(mapper, sessions, "");
    private final FireAlarmControl fire = new FireAlarmControl(alarm, sessions, "", false);
    private final BlindCommands blind = new BlindCommands(mapper, sessions, "");
    private final BlindAutoControl blindAuto = new BlindAutoControl(blind, sessions, "", false, Runnable::run);
    private final DeviceWebSocketHandler handler = new DeviceWebSocketHandler(mapper, sensors, sessions,
            new LightCommandSender(mapper, sessions, Optional.empty(), "", 0),
            new SensorSampleReceiver(mapper, new SensorIngestion(sensors, Optional.empty()), sessions,
                    new SimpleMeterRegistry(), airDust, fire, blindAuto, "", "", ""), servo, airDust, alarm, fire, blind);

    @Test
    void legacySensorFieldsUpdateTogetherAndKeepOtherValues() throws Exception {
        var client = mock(WebSocketSession.class);
        handler.handleMessage(client, new TextMessage("""
                {"dust":23.456,"humidity":42,"light":"350","unknown":100}
                """));

        assertEquals(Map.of("temperature", 20.0, "humidity", 42.0, "gas", 10.0,
                "light", 350.0, "dust", 23.46, "infra", 1000.0), sensors.read());
    }

    @Test
    void malformedSensorMessageLeavesTheSnapshotIntact() throws Exception {
        var before = sensors.read();
        var client = mock(WebSocketSession.class);
        handler.handleMessage(client, new TextMessage("""
                {"humidity":42,"dust":null}
                """));

        assertEquals(before, sensors.read());
        verify(client).sendMessage(argThat(message -> message.getPayload().equals(
                "{\"status\":\"error\",\"message\":\"Invalid JSON\"}")));
    }

    @Test
    void closingAnOldConnectionKeepsTheReplacementRegistered() {
        var old = session("old");
        var replacement = session("replacement");
        handler.afterConnectionEstablished(old);
        handler.afterConnectionEstablished(replacement);

        handler.afterConnectionClosed(old, CloseStatus.NORMAL);
        assertEquals(Map.of("127.0.0.1", "replacement"), sessions.activeIdentities());

        handler.afterConnectionClosed(replacement, CloseStatus.NORMAL);
        assertEquals(Map.of(), sessions.activeIdentities());
    }

    private WebSocketSession session(String id) {
        var client = mock(WebSocketSession.class);
        when(client.getId()).thenReturn(id);
        when(client.getRemoteAddress()).thenReturn(new InetSocketAddress("127.0.0.1", 9000));
        when(client.isOpen()).thenReturn(true);
        return client;
    }
}
