package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.net.InetSocketAddress;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class FireAlarmCommandsTest {
    @Test
    void requiresTheFireSessionAndABooleanMatchingTheCommand() throws Exception {
        var mapper = new ObjectMapper();
        var sessions = new DeviceSessions();
        var fire = register(sessions, "127.0.0.1");
        var air = register(sessions, "127.0.0.2");
        var commands = new FireAlarmCommands(mapper, sessions, "127.0.0.1");
        var first = commands.send(true);

        var sent = ArgumentCaptor.forClass(TextMessage.class);
        verify(fire).sendMessage(sent.capture());
        var frame = (ObjectNode) mapper.readTree(sent.getValue().getPayload());
        assertEquals("FireAlarmCommand", frame.path("type").textValue());
        assertEquals("firepi", frame.path("nodeId").textValue());
        frame.put("type", "FireAlarmCommandApplied");
        var payload = (ObjectNode) frame.path("payload");
        var commandId = payload.path("commandId").textValue();

        commands.handle(frame, air);
        payload.put("on", "true");
        commands.handle(frame, fire);
        payload.put("on", false);
        commands.handle(frame, fire);
        assertFalse(first.isDone());

        payload.put("on", true);
        commands.handle(frame, fire);
        assertEquals(new FireAlarmCommands.Receipt(commandId, true, "applied"), first.get(1, TimeUnit.SECONDS));

        var next = commands.send(false);
        commands.handle(frame, fire);
        assertFalse(next.isDone());
        commands.disconnected(fire);
        var result = next.get(1, TimeUnit.SECONDS);
        assertNotEquals(commandId, result.commandId());
        assertFalse(result.on());
        assertEquals("unconfirmed", result.status());
    }

    private WebSocketSession register(DeviceSessions sessions, String address) {
        var session = mock(WebSocketSession.class);
        when(session.isOpen()).thenReturn(true);
        when(session.getRemoteAddress()).thenReturn(new InetSocketAddress(address, 9000));
        sessions.register(session);
        return session;
    }
}
