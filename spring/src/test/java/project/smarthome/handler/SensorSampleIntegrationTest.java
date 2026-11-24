package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import project.smarthome.telemetry.SensorSnapshot;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"server.address=127.0.0.1", "PILAB_AIR_IP=127.0.0.1", "PILAB_FIRE_IP=127.0.0.2"})
@ExtendWith(OutputCaptureExtension.class)
class SensorSampleIntegrationTest {
    private static final String RUN = "0123456789abcdef0123456789abcdef";
    private static final String NEXT_RUN = "11111111111111111111111111111111";
    private static final String LOG_PREFIX = "{\"event\":\"sensor_sample\"";

    @LocalServerPort
    private int port;

    @Autowired
    private SensorSnapshot sensors;

    @Autowired
    private ObjectMapper mapper;

    @Autowired
    private MeterRegistry meters;

    @Test
    void recordsOnlyNewSamplesFromTheConfiguredNodeAcrossReconnect(CapturedOutput output) throws Exception {
        var replies = new LinkedBlockingQueue<String>();
        Instant before = Instant.now();
        assertEquals(0.0, connected("airpi"));
        assertEquals(0.0, lastSample("airpi"));
        double firstSampleTimestamp;
        try (var air = connect(replies)) {
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> assertEquals(1.0, connected("airpi")));
            assertEquals(0.0, lastSample("airpi"));
            air.sendMessage(sample("airpi", RUN, 13, "{\"dust\":23.5}"));
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> assertEquals(1, sampleLogs(output).size()));
            assertEquals(23.5, sensors.read().get("dust"));
            firstSampleTimestamp = lastSample("airpi");
            assertTrue(firstSampleTimestamp > 0);
        }
        await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> assertEquals(0.0, connected("airpi")));
        assertEquals(firstSampleTimestamp, lastSample("airpi"));

        try (var air = connect(replies)) {
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> assertEquals(1.0, connected("airpi")));
            assertEquals(firstSampleTimestamp, lastSample("airpi"));
            air.sendMessage(sample("airpi", RUN, 13, "{\"dust\":99}"));
            air.sendMessage(sample("airpi", RUN, 12, "{\"dust\":88}"));
            air.sendMessage(sample("firepi", RUN, 1, "{\"infra\":7,\"gas\":8}"));
            assertError(replies, "Unexpected sensor session");
            assertEquals(23.5, sensors.read().get("dust"));
            assertEquals(1000.0, sensors.read().get("infra"));
            assertEquals(1, sampleLogs(output).size());
            assertEquals(firstSampleTimestamp, lastSample("airpi"));
            assertEquals(0.0, connected("firepi"));
            assertEquals(0.0, lastSample("firepi"));

            air.sendMessage(new TextMessage("""
                    {"type":"SensorSample","nodeId":"airpi","runId":11111111111111111111111111111111,
                    "payload":{"seq":14,"values":{"dust":99}}}
                    """));
            assertError(replies, "Invalid SensorSample runId");
            air.sendMessage(sample("airpi", RUN, 14, "{\"dust\":23.5,\"gas\":8}"));
            assertError(replies, "Invalid SensorSample values");
            assertEquals(10.0, sensors.read().get("gas"));
            air.sendMessage(sample("airpi", RUN, 14, "{\"dust\":99,\"temperature\":24.1,\"humidity\":101}"));
            assertError(replies, "Invalid SensorSample values");
            assertEquals(23.5, sensors.read().get("dust"));
            assertEquals(20.0, sensors.read().get("temperature"));
            air.sendMessage(sample("airpi", RUN, 14, "{\"dust\":23.5}"));
            air.sendMessage(sample("airpi", NEXT_RUN, 1, "{\"dust\":40,\"temperature\":24.1,\"humidity\":42}"));
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() -> {
                assertEquals(40.0, sensors.read().get("dust"));
                assertEquals(24.1, sensors.read().get("temperature"));
                assertEquals(42.0, sensors.read().get("humidity"));
                assertEquals(3, sampleLogs(output).size());
            });
        }

        var logs = sampleLogs(output);
        var first = mapper.readTree(logs.get(0));
        var next = mapper.readTree(logs.get(1));
        var restarted = mapper.readTree(logs.get(2));
        assertEquals("airpi", first.path("nodeId").asText());
        assertEquals(RUN, first.path("runId").asText());
        assertEquals(13, first.path("seq").asInt());
        assertEquals(23.5, first.path("values").path("dust").asDouble());
        assertTrue(!Instant.parse(first.path("receivedAt").asText()).isBefore(before));
        assertEquals(14, next.path("seq").asInt());
        assertEquals(first.path("values"), next.path("values"));
        assertEquals(NEXT_RUN, restarted.path("runId").asText());
        assertEquals(1, restarted.path("seq").asInt());
        assertEquals(24.1, restarted.path("values").path("temperature").asDouble());
        assertEquals(42.0, restarted.path("values").path("humidity").asDouble());
        assertEquals(Instant.parse(restarted.path("receivedAt").asText()).toEpochMilli() / 1000.0, lastSample("airpi"));
    }

    private double connected(String node) {
        return meters.get("pilab.sensor.websocket.connected").tag("node", node).gauge().value();
    }

    private double lastSample(String node) {
        return meters.get("pilab.sensor.last.sample.timestamp").tag("node", node).gauge().value();
    }

    private WebSocketSession connect(LinkedBlockingQueue<String> replies) throws Exception {
        var handler = new TextWebSocketHandler() {
            @Override
            protected void handleTextMessage(WebSocketSession session, TextMessage message) {
                replies.add(message.getPayload());
            }
        };
        return new StandardWebSocketClient().execute(handler, "ws://127.0.0.1:" + port + "/socket")
                .get(5, TimeUnit.SECONDS);
    }

    private void assertError(LinkedBlockingQueue<String> replies, String reason) throws Exception {
        String reply = replies.poll(5, TimeUnit.SECONDS);
        assertNotNull(reply);
        assertEquals(reason, mapper.readTree(reply).path("message").asText());
    }

    private static List<String> sampleLogs(CapturedOutput output) {
        return output.getAll().lines().filter(line -> line.contains(LOG_PREFIX))
                .map(line -> line.substring(line.indexOf(LOG_PREFIX))).toList();
    }

    private static TextMessage sample(String node, String run, long sequence, String values) {
        return new TextMessage("""
                {"type":"SensorSample","nodeId":"%s","runId":"%s","payload":{"seq":%d,"values":%s}}
                """.formatted(node, run, sequence, values));
    }
}
