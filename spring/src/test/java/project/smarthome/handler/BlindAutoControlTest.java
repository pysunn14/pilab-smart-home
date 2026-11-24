package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.net.InetSocketAddress;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class BlindAutoControlTest {
    private static final String ADDRESS = "127.0.0.1";
    private final ObjectMapper mapper = new ObjectMapper();
    private final DeviceSessions sessions = new DeviceSessions();
    private final BlindCommands commands = new BlindCommands(mapper, sessions, ADDRESS);
    private final List<JsonNode> sent = new ArrayList<>();
    private final WebSocketSession source = mock(WebSocketSession.class);
    private final BlindAutoControl control = new BlindAutoControl(commands, sessions, ADDRESS, true, Runnable::run);

    @BeforeEach
    void connect() throws Exception {
        when(source.getRemoteAddress()).thenReturn(new InetSocketAddress(ADDRESS, 9000));
        when(source.isOpen()).thenReturn(true);
        doAnswer(call -> {
            TextMessage frame = call.getArgument(0);
            sent.add(mapper.readTree(frame.getPayload()).path("payload"));
            return null;
        }).when(source).sendMessage(any());
        sessions.register(source);
    }

    @Test
    void waitsForNeutralBeforeAnotherDirection() {
        sample(500, 180);
        assertDirections("UP");
        acknowledge(0, "Applied");
        sample(190, 800);
        assertDirections("UP");
        sample(200, 750, 800);
        assertDirections("UP", "DOWN");
        acknowledge(1, "Applied");
    }

    @Test
    void rearmingAllowsTheSameDirectionEvenAfterAnUnconfirmedOutput() {
        sample(199.9);
        acknowledge(0, "Unconfirmed");
        sample(190, 800);
        assertDirections("UP");
        sample(500, 180);
        assertDirections("UP", "UP");
        acknowledge(1, "Applied");
    }

    @Test
    void dispatchesTheRearmedCommandAfterTheMatchingReceipt() {
        sample(180, 750, 750.1);
        assertDirections("UP");
        receipt(0, "DOWN", "Applied");
        assertDirections("UP");
        acknowledge(0, "Applied");
        assertDirections("UP", "DOWN");
        acknowledge(1, "Applied");
    }

    @Test
    void neutralDiscardsADeferredCommandBeforeItIsSent() {
        sample(180, 500, 800, 500);
        acknowledge(0, "Applied");
        assertDirections("UP");
    }

    @Test
    void disabledAndReplacedSessionsDoNotIssueCommands() {
        var disabled = new BlindAutoControl(commands, sessions, ADDRESS, false, Runnable::run);
        disabled.sample(source, 180);
        assertDirections();

        var replacement = mock(WebSocketSession.class);
        when(replacement.getRemoteAddress()).thenReturn(new InetSocketAddress(ADDRESS, 9001));
        when(replacement.isOpen()).thenReturn(true);
        sessions.register(replacement);
        control.sample(source, 180);
        assertDirections();
    }

    private void sample(double... values) {
        for (double value : values) {
            control.sample(source, value);
        }
    }

    private void assertDirections(String... expected) {
        assertEquals(List.of(expected), sent.stream().map(row -> row.path("direction").asText()).toList());
    }

    private void acknowledge(int index, String result) {
        receipt(index, sent.get(index).path("direction").asText(), result);
    }

    private void receipt(int index, String direction, String result) {
        var message = mapper.createObjectNode().put("type", "BlindCommand" + result).put("nodeId", "lightpi");
        message.putObject("payload").put("commandId", sent.get(index).path("commandId").asText())
                .put("direction", direction);
        commands.handle(message, source);
    }
}
