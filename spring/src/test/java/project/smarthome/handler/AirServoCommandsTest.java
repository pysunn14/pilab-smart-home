package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.net.InetSocketAddress;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AirServoCommandsTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private final DeviceSessions sessions = new DeviceSessions();
    private final AirServoCommands servo = new AirServoCommands(mapper, sessions, "127.0.0.1");

    @Test
    void verifiesSessionCommandIdAndAngleBeforeConfirming() throws Exception {
        var air = session("127.0.0.1");
        var fire = session("127.0.0.2");
        var result = servo.send(0);
        assertThrows(ResponseStatusException.class, () -> servo.send(90));

        var sent = ArgumentCaptor.forClass(TextMessage.class);
        verify(air).sendMessage(sent.capture());
        var acknowledgment = (ObjectNode) mapper.readTree(sent.getValue().getPayload());
        assertEquals("ServoCommand", acknowledgment.path("type").textValue());
        acknowledgment.put("type", "ServoCommandApplied");
        var payload = (ObjectNode) acknowledgment.path("payload");
        var id = payload.path("commandId").textValue();

        servo.handle(acknowledgment, fire);
        assertFalse(result.isDone());
        payload.put("commandId", "different");
        servo.handle(acknowledgment, air);
        assertFalse(result.isDone());
        payload.put("commandId", id).put("angle", 90);
        servo.handle(acknowledgment, air);
        assertFalse(result.isDone());
        payload.put("angle", 0);
        servo.handle(acknowledgment, air);
        assertEquals(new AirServoCommands.Receipt(id, 0, "applied"), result.get(1, TimeUnit.SECONDS));
    }

    @Test
    void disconnectLeavesTheAngleUnconfirmed() throws Exception {
        var air = session("127.0.0.1");
        var result = servo.send(90);
        sessions.unregister(air);
        servo.disconnected(air);
        assertEquals("unconfirmed", result.get(1, TimeUnit.SECONDS).status());
        assertThrows(ResponseStatusException.class, () -> servo.send(0));
    }

    private WebSocketSession session(String address) {
        var session = mock(WebSocketSession.class);
        when(session.isOpen()).thenReturn(true);
        when(session.getRemoteAddress()).thenReturn(new InetSocketAddress(address, 9000));
        sessions.register(session);
        return session;
    }
}
