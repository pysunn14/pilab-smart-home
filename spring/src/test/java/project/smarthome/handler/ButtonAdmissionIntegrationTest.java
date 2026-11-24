package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import project.smarthome.control.ButtonEventId;
import project.smarthome.control.ControlService;
import project.smarthome.control.LightControl;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"server.address=127.0.0.1", "PILAB_LIGHT_IP=127.0.0.1",
                "pilab.control.light.queue-capacity=2"})
@ExtendWith(OutputCaptureExtension.class)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)
class ButtonAdmissionIntegrationTest {
    private static final String RUN_ID = "0123456789abcdef0123456789abcdef";
    private static final String LIGHT_RUN_ID = "11111111111111111111111111111111";
    private static final String RESTARTED_LIGHT_RUN_ID = "22222222222222222222222222222222";

    @LocalServerPort
    private int port;

    @Autowired
    private ControlService control;

    @Autowired
    private DeviceSessions sessions;

    @Test
    void waitsForMatchingAppliedReplyBeforeSendingNextCommand(CapturedOutput output) throws Exception {
        var frames = new LinkedBlockingQueue<String>();
        var client = new StandardWebSocketClient();
        try (var main = connect(client); var light = connect(client, recordFrames(frames))) {
            light.sendMessage(report(false));
            waitForLog(output, "on=false result=INITIALIZED");

            main.sendMessage(press(1));
            var first = frames.poll(5, TimeUnit.SECONDS);
            assertNotNull(first, "LightPi should receive the first command");
            assertCommand(first, 1, true);

            main.sendMessage(press(2));
            main.sendMessage(press(3));
            waitForLog(output, "seq=2 result=ACCEPTED");
            waitForLog(output, "seq=3 result=QUEUE_FULL");
            light.sendMessage(applied(2, false));
            waitForLog(output, "result=NOT_PENDING");
            assertNull(frames.poll(200, TimeUnit.MILLISECONDS), "Second command must wait for the first reply");
            assertEquals(2, control.lightSnapshot().orElseThrow().pending().size());

            light.sendMessage(applied(1, true));
            var second = frames.poll(5, TimeUnit.SECONDS);
            assertNotNull(second, "LightPi should receive the second command after the reply");
            assertCommand(second, 2, false);

            light.sendMessage(applied(2, false));
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() ->
                    assertEquals(new LightControl.Snapshot(false, List.of()), control.lightSnapshot().orElseThrow()));
        }
    }

    @ParameterizedTest
    @CsvSource({"2,true,false", "1,false,false", ",false,true"})
    void reconnectHoldsQueueWhenTheSnapshotCannotResolveIt(Long lastApplied, boolean on,
                                                           boolean restarted, CapturedOutput output)
            throws Exception {
        var frames = new LinkedBlockingQueue<String>();
        var client = new StandardWebSocketClient();
        try (var main = connect(client)) {
            try (var light = connect(client, recordFrames(frames))) {
                light.sendMessage(report(false));
                waitForLog(output, "on=false result=INITIALIZED");
                main.sendMessage(press(1));
                var first = frames.poll(5, TimeUnit.SECONDS);
                assertNotNull(first);
                assertCommand(first, 1, true);
                main.sendMessage(press(2));
                waitForLog(output, "seq=2 result=ACCEPTED");
            }
            waitForLog(output, "light_dispatch_suspended reason=DISCONNECTED");
            try (var replacement = connect(client, recordFrames(frames))) {
                var stateReport = report(!restarted, restarted ? RESTARTED_LIGHT_RUN_ID : LIGHT_RUN_ID);
                replacement.sendMessage(stateReport);
                waitForLog(output, "result=RECOVERING");
                var query = frames.poll(5, TimeUnit.SECONDS);
                assertNotNull(query);
                var mapper = new ObjectMapper();
                assertEquals(mapper.readTree("""
                        {"type":"LightStateQuery","nodeId":"lightpi","payload":{"queryId":"mainpi/%s/1"}}
                        """.formatted(RUN_ID)), mapper.readTree(query));
                replacement.sendMessage(applied(1, true));
                waitForLog(output, "result=NOT_PENDING");
                assertNull(frames.poll(200, TimeUnit.MILLISECONDS), "An ordinary ACK cannot finish the state query");
                assertEquals(2, control.lightSnapshot().orElseThrow().pending().size());

                replacement.sendMessage(snapshot(lastApplied, on));
                waitForLog(output, "result=MISMATCH");
                replacement.sendMessage(stateReport);
                waitForLog(output, "result=ALREADY_INITIALIZED");
                replacement.sendMessage(snapshot(1L, true));
                waitForLog(output, "result=NO_MATCHING_QUERY");
                assertNull(frames.poll(200, TimeUnit.MILLISECONDS), "A mismatching query must keep the queue held");
                assertEquals(2, control.lightSnapshot().orElseThrow().pending().size());
            }
        }
    }

    @ParameterizedTest
    @ValueSource(booleans = {true, false})
    void firstReportEnablesControlAndCannotOverwriteItsTarget(boolean initialOn, CapturedOutput output)
            throws Exception {
        var client = new StandardWebSocketClient();
        try (var main = connect(client)) {
            await().atMost(Duration.ofSeconds(5)).until(() -> !sessions.activeIdentities().isEmpty());
            var originalSession = sessions.activeIdentities();
            try (var light = connect(client)) {
                await().atMost(Duration.ofSeconds(5)).until(() -> !sessions.activeIdentities().equals(originalSession));
                main.sendMessage(report(initialOn)); // An older socket at this IP must not initialize the light.
                main.sendMessage(press(1));
                waitForLog(output, "seq=1 result=CONTROL_UNAVAILABLE");
                assertTrue(control.lightSnapshot().isEmpty());

                light.sendMessage(report(initialOn));
                waitForLog(output, "on=" + initialOn + " result=INITIALIZED");
                main.sendMessage(press(2));
                main.sendMessage(press(2));
                waitForLog(output, "seq=2 result=DUPLICATE");

                light.sendMessage(report(initialOn));
                waitForLog(output, "on=" + initialOn + " result=ALREADY_INITIALIZED");
                main.sendMessage(press(3));

                var expected = new LightControl.Snapshot(initialOn, List.of(
                        new LightControl.Command(new ButtonEventId("mainpi", RUN_ID, 2), !initialOn),
                        new LightControl.Command(new ButtonEventId("mainpi", RUN_ID, 3), initialOn)));
                await().atMost(Duration.ofSeconds(5)).untilAsserted(() ->
                        assertEquals(expected, control.lightSnapshot().orElseThrow()));
            }
        }
    }

    private WebSocketSession connect(StandardWebSocketClient client) throws Exception {
        return connect(client, new TextWebSocketHandler());
    }

    private WebSocketSession connect(StandardWebSocketClient client, TextWebSocketHandler handler) throws Exception {
        return client.execute(handler, "ws://127.0.0.1:" + port + "/socket")
                .get(5, TimeUnit.SECONDS);
    }

    private static TextWebSocketHandler recordFrames(LinkedBlockingQueue<String> frames) {
        return new TextWebSocketHandler() {
            @Override
            protected void handleTextMessage(WebSocketSession session, TextMessage message) {
                frames.add(message.getPayload());
            }
        };
    }

    private static void assertCommand(String frame, long sequence, boolean on) throws Exception {
        var mapper = new ObjectMapper();
        assertEquals(mapper.readTree("""
                {"type":"LightCommand","nodeId":"lightpi","payload":{"commandId":"mainpi/%s/%d","on":%b}}
                """.formatted(RUN_ID, sequence, on)), mapper.readTree(frame));
    }

    private static TextMessage applied(long sequence, boolean on) {
        return new TextMessage("""
                {"type":"LightCommandApplied","nodeId":"lightpi","payload":{"commandId":"mainpi/%s/%d","on":%b}}
                """.formatted(RUN_ID, sequence, on));
    }

    private static TextMessage snapshot(Long lastApplied, boolean on) {
        String lastId = lastApplied == null ? "null" : "\"mainpi/%s/%d\"".formatted(RUN_ID, lastApplied);
        return new TextMessage("""
                {"type":"LightStateSnapshot","nodeId":"lightpi",
                "payload":{"queryId":"mainpi/%s/1","lastAppliedCommandId":%s,"on":%b}}
                """.formatted(RUN_ID, lastId, on));
    }

    private static void waitForLog(CapturedOutput output, String text) {
        await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> assertTrue(output.getAll().contains(text), text));
    }

    private static TextMessage press(long sequence) {
        return new TextMessage("""
                {"type":"ButtonPressed","nodeId":"mainpi","runId":"%s","payload":{"seq":%d}}
                """.formatted(RUN_ID, sequence));
    }

    private static TextMessage report(boolean on) {
        return report(on, LIGHT_RUN_ID);
    }

    private static TextMessage report(boolean on, String runId) {
        return new TextMessage("""
                {"type":"LightStateReported","nodeId":"lightpi","runId":"%s","payload":{"on":%b}}
                """.formatted(runId, on));
    }
}
