package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.socket.WebSocketSession;

import java.net.InetSocketAddress;
import java.util.concurrent.CompletableFuture;

import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.same;
import static org.mockito.Mockito.after;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.timeout;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class AirDustControlTest {
    private final DeviceSessions sessions = new DeviceSessions();
    private final AirServoCommands servo = mock(AirServoCommands.class);
    private final AirDustControl control = new AirDustControl(servo, sessions, "127.0.0.1", true);
    private final ObjectMapper mapper = new ObjectMapper();

    @AfterEach
    void stopWorker() {
        control.close();
    }

    @Test
    void waitsForCurrentServoReportAndKeepsLatestGoalWhileCommandIsPending() throws Exception {
        var session = session("127.0.0.1");
        var other = session("127.0.0.2");
        var first = new CompletableFuture<AirServoCommands.Receipt>();
        when(servo.send(0, session)).thenReturn(first);
        when(servo.send(90, session)).thenReturn(CompletableFuture.completedFuture(receipt(90, "applied")));

        control.sample(session, 100);
        control.report(report(90), other);
        verify(servo, after(100).never()).send(anyInt(), same(session));
        control.report(report(90), session);
        verify(servo, after(100).never()).send(anyInt(), same(session));
        control.sample(session, 120);
        verify(servo, timeout(1000).times(1)).send(0, session);
        control.sample(session, 120);
        control.sample(session, 80);
        verify(servo, after(100).never()).send(90, session);
        first.complete(receipt(0, "applied"));
        verify(servo, timeout(1000).times(1)).send(90, session);
        verify(servo, after(100).times(1)).send(0, session);
    }

    @Test
    void leavesUnconfirmedGoalUntilItChangesAndUsesNewConnectionReport() throws Exception {
        var previous = session("127.0.0.1");
        when(servo.send(0, previous)).thenReturn(CompletableFuture.completedFuture(receipt(0, "unconfirmed")));
        control.report(report(90), previous);
        control.sample(previous, 120);
        verify(servo, timeout(1000).times(1)).send(0, previous);
        control.sample(previous, 120);
        verify(servo, after(100).times(1)).send(0, previous);

        var replacement = session("127.0.0.1");
        when(servo.send(90, replacement)).thenReturn(CompletableFuture.completedFuture(receipt(90, "applied")));
        control.report(report(0), replacement);
        control.sample(previous, 120);
        control.sample(replacement, 80);
        verify(servo, timeout(1000).times(1)).send(90, replacement);
        verify(servo, after(100).never()).send(0, replacement);
    }

    private com.fasterxml.jackson.databind.JsonNode report(int angle) {
        var frame = mapper.createObjectNode().put("type", "ServoStateReported").put("nodeId", "airpi");
        frame.putObject("payload").put("angle", angle);
        return frame;
    }

    private AirServoCommands.Receipt receipt(int angle, String status) {
        return new AirServoCommands.Receipt("test-command", angle, status);
    }

    private WebSocketSession session(String address) {
        var session = mock(WebSocketSession.class);
        when(session.isOpen()).thenReturn(true);
        when(session.getRemoteAddress()).thenReturn(new InetSocketAddress(address, 9000));
        sessions.register(session);
        return session;
    }
}
