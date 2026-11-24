package project.smarthome.handler;

import io.micrometer.core.instrument.MeterRegistry;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import project.smarthome.SmarthomeApplication;
import project.smarthome.telemetry.SensorSnapshot;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

import static org.awaitility.Awaitility.await;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SensorSamplePersistenceIntegrationTest {
    private static final String RUN = "0123456789abcdef0123456789abcdef";
    private static final String SEQUENCE = "18446744073709551614";
    private static final String NEXT_SEQUENCE = "18446744073709551615";

    @TempDir
    Path directory;

    @Test
    void sqliteCommitControlsAdmissionAndDeduplicationSurvivesRestart() throws Exception {
        Path database = directory.resolve("samples.sqlite");
        var replies = new LinkedBlockingQueue<String>();
        try (var app = start(database)) {
            assertTrue(Files.isRegularFile(database), "Spring should open the configured SQLite file");
            try (var db = DriverManager.getConnection("jdbc:sqlite:" + database);
                 var air = connect(app, replies)) {
                var snapshot = app.getBean(SensorSnapshot.class);
                execute(db, """
                        CREATE TRIGGER reject_sample BEFORE INSERT ON sensor_samples
                        BEGIN SELECT RAISE(ABORT, 'test write failure'); END
                        """);
                air.sendMessage(sample(SEQUENCE, "{\"dust\":99}"));
                assertEquals("{\"status\":\"error\",\"message\":\"Sensor sample storage failed\"}",
                        replies.poll(5, TimeUnit.SECONDS));
                assertEquals(10.0, snapshot.read().get("dust"));
                assertEquals(0, count(db));
                assertStorageMetric(app, "failed", 1);
                assertEquals(0.0, lastSample(app));

                execute(db, "DROP TRIGGER reject_sample");
                air.sendMessage(sample(SEQUENCE, "{\"dust\":23.5}"));
                await().atMost(Duration.ofSeconds(5)).untilAsserted(() ->
                        assertEquals(23.5, snapshot.read().get("dust")));
                assertStoredSample(db);
                assertStorageMetric(app, "stored", 1);
                await().atMost(Duration.ofSeconds(5)).until(() -> lastSample(app) > 0);
            }
        }

        try (var app = start(database);
             var db = DriverManager.getConnection("jdbc:sqlite:" + database);
             var air = connect(app, replies)) {
            var snapshot = app.getBean(SensorSnapshot.class);
            assertStoredSample(db);

            air.sendMessage(sample(SEQUENCE, "{\"dust\":77}"));
            air.sendMessage(sample("1", "{\"dust\":88}"));
            air.sendMessage(sample(NEXT_SEQUENCE, "{}")); // Wait for the valid preceding frames to be processed.
            assertEquals("{\"status\":\"error\",\"message\":\"Invalid SensorSample values\"}",
                    replies.poll(5, TimeUnit.SECONDS));
            assertEquals(10.0, snapshot.read().get("dust"));
            assertStoredSample(db);
            assertStorageMetric(app, "ignored", 2);

            air.sendMessage(sample(NEXT_SEQUENCE, "{\"dust\":41.2}"));
            await().atMost(Duration.ofSeconds(5)).untilAsserted(() ->
                    assertEquals(41.2, snapshot.read().get("dust")));
            assertEquals(2, count(db));
        }
    }

    private static ServletWebServerApplicationContext start(Path database) {
        return (ServletWebServerApplicationContext) new SpringApplicationBuilder(SmarthomeApplication.class)
                .run("--server.address=127.0.0.1", "--server.port=0", "--PILAB_AIR_IP=127.0.0.1",
                        "--management.endpoints.web.exposure.include=prometheus",
                        "--PILAB_SENSOR_DB_PATH=" + database);
    }

    private static WebSocketSession connect(ServletWebServerApplicationContext app,
                                            LinkedBlockingQueue<String> replies) throws Exception {
        var client = new TextWebSocketHandler() {
            @Override
            protected void handleTextMessage(WebSocketSession session, TextMessage message) {
                replies.add(message.getPayload());
            }
        };
        return new StandardWebSocketClient().execute(client,
                "ws://127.0.0.1:" + app.getWebServer().getPort() + "/socket").get(5, TimeUnit.SECONDS);
    }

    private static TextMessage sample(String seq, String values) {
        return new TextMessage("""
                {"type":"SensorSample","nodeId":"airpi","runId":"%s","payload":{"seq":%s,"values":%s}}
                """.formatted(RUN, seq, values));
    }

    private static void execute(Connection db, String sql) throws Exception {
        try (var statement = db.createStatement()) {
            statement.execute(sql);
        }
    }

    private static int count(Connection db) throws Exception {
        try (var statement = db.createStatement();
             var rows = statement.executeQuery("SELECT COUNT(*) FROM sensor_samples")) {
            assertTrue(rows.next());
            return rows.getInt(1);
        }
    }

    private static void assertStorageMetric(ServletWebServerApplicationContext app, String outcome, int count)
            throws Exception {
        var request = HttpRequest.newBuilder(URI.create(
                "http://127.0.0.1:" + app.getWebServer().getPort() + "/actuator/prometheus"))
                .timeout(Duration.ofSeconds(5)).build();
        var response = HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.ofString());
        assertEquals(200, response.statusCode());
        String metric = "pilab_sensor_sqlite_write_seconds";
        var lines = response.body().lines()
                .filter(line -> line.contains("node=\"airpi\"") && line.contains("result=\"" + outcome + "\"")).toList();
        var counter = lines.stream().filter(line -> line.startsWith(metric + "_count{"))
                .findFirst().orElseThrow();
        assertEquals(count, Double.parseDouble(counter.substring(counter.lastIndexOf('}') + 1).trim()));
        assertTrue(lines.stream().anyMatch(line -> line.startsWith(metric + "_bucket{")));
    }

    private static double lastSample(ServletWebServerApplicationContext app) {
        return app.getBean(MeterRegistry.class).get("pilab.sensor.last.sample.timestamp")
                .tag("node", "airpi").gauge().value();
    }

    private static void assertStoredSample(Connection db) throws Exception {
        try (var statement = db.createStatement();
             var rows = statement.executeQuery("SELECT node_id, run_id, seq, received_at, values_json FROM sensor_samples")) {
            assertTrue(rows.next());
            assertEquals("airpi", rows.getString("node_id"));
            assertEquals(RUN, rows.getString("run_id"));
            assertEquals(SEQUENCE, rows.getString("seq"));
            assertTrue(Instant.parse(rows.getString("received_at")).isAfter(Instant.EPOCH));
            assertEquals("{\"dust\":23.5}", rows.getString("values_json"));
            assertFalse(rows.next());
        }
    }
}
