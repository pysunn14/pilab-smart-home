package project.smarthome.telemetry;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.Meter.MeterProvider;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.time.Duration;
import java.util.Locale;

@Component
@ConditionalOnProperty(name = "PILAB_SENSOR_DB_PATH")
public class SqliteSensorHistory {
    private static final String CREATE_TABLE = """
            CREATE TABLE IF NOT EXISTS sensor_samples (
                node_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                seq TEXT NOT NULL,
                received_at TEXT NOT NULL,
                values_json TEXT NOT NULL,
                PRIMARY KEY (node_id, run_id, seq)
            )
            """;

    private static final String APPEND = """
            INSERT INTO sensor_samples (node_id, run_id, seq, received_at, values_json)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM sensor_samples WHERE node_id = ? AND run_id = ? AND seq >= ?
            )
            """;

    private final Connection connection;
    private final ObjectMapper mapper;
    private final MeterRegistry registry;
    private final MeterProvider<Timer> writeTimers;

    public SqliteSensorHistory(@Value("${PILAB_SENSOR_DB_PATH}") String path, ObjectMapper mapper,
                               MeterRegistry registry)
            throws SQLException {
        if (path.isBlank()) {
            throw new IllegalArgumentException("PILAB_SENSOR_DB_PATH must name a SQLite file");
        }
        this.mapper = mapper;
        this.registry = registry;
        this.writeTimers = Timer.builder("pilab.sensor.sqlite.write")
                .description("Time to check and commit a sensor sample in SQLite")
                .publishPercentileHistogram()
                .minimumExpectedValue(Duration.ofNanos(10_000))
                .withRegistry(registry);
        this.connection = open(Path.of(path).toAbsolutePath());
    }

    private static Connection open(Path path) throws SQLException {
        var connection = DriverManager.getConnection("jdbc:sqlite:" + path);
        try (var statement = connection.createStatement()) {
            try (var result = statement.executeQuery("PRAGMA journal_mode=WAL")) {
                if (!result.next() || !"wal".equalsIgnoreCase(result.getString(1))) {
                    throw new SQLException("Could not enable SQLite WAL");
                }
            }
            statement.execute("PRAGMA synchronous=FULL");
            statement.execute(CREATE_TABLE);
            return connection;
        } catch (SQLException error) {
            try {
                connection.close();
            } catch (SQLException closeError) {
                error.addSuppressed(closeError);
            }
            throw error;
        }
    }

    synchronized boolean appendIfNew(SensorSample sample) throws SQLException {
        var duration = Timer.start(registry);
        String result = "failed";
        // SQLite INTEGER is signed. Fixed-width decimal text preserves unsigned order in the primary key.
        String sequence = String.format(Locale.ROOT, "%020d", sample.sequence());
        try (var statement = connection.prepareStatement(APPEND)) {
            statement.setString(1, sample.nodeId());
            statement.setString(2, sample.runId());
            statement.setString(3, sequence);
            statement.setString(4, sample.receivedAt().toString());
            statement.setString(5, mapper.valueToTree(sample.values()).toString());
            statement.setString(6, sample.nodeId());
            statement.setString(7, sample.runId());
            statement.setString(8, sequence);
            // Autocommit keeps the freshness check and row insertion in one transaction.
            boolean stored = statement.executeUpdate() == 1;
            result = stored ? "stored" : "ignored";
            return stored;
        } finally {
            duration.stop(writeTimers.withTags("node", sample.nodeId(), "result", result));
        }
    }

    @PreDestroy
    synchronized void close() throws SQLException {
        connection.close();
    }
}
