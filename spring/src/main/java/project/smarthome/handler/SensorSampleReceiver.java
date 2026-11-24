package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;
import project.smarthome.telemetry.SensorIngestion;
import project.smarthome.telemetry.SensorSample;

import java.io.IOException;
import java.sql.SQLException;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.regex.Pattern;

@Component
final class SensorSampleReceiver {
    private static final Logger LOG = Logger.getLogger(SensorSampleReceiver.class.getName());
    private static final Pattern RUN_ID = Pattern.compile("[0-9a-f]{32}");

    private final ObjectMapper mapper;
    private final SensorIngestion ingestion;
    private final DeviceSessions sessions;
    private final AirDustControl airDust;
    private final FireAlarmControl fireControl;
    private final BlindAutoControl blindAuto;
    private final Map<String, Source> sources;

    SensorSampleReceiver(ObjectMapper mapper, SensorIngestion ingestion, DeviceSessions sessions, MeterRegistry meters,
                         AirDustControl airDust, FireAlarmControl fireControl, BlindAutoControl blindAuto,
                         @Value("${PILAB_AIR_IP:}") String airAddress,
                         @Value("${PILAB_FIRE_IP:}") String fireAddress,
                         @Value("${PILAB_LIGHT_IP:}") String lightAddress) {
        this.mapper = mapper;
        this.ingestion = ingestion;
        this.sessions = sessions;
        this.airDust = airDust;
        this.fireControl = fireControl;
        this.blindAuto = blindAuto;
        this.sources = Map.of(
                "airpi", new Source(airAddress, Set.of("dust", "temperature", "humidity")),
                "firepi", new Source(fireAddress, Set.of("infra", "gas")),
                "lightpi", new Source(lightAddress, Set.of("light")));
        registerHealthMetrics(meters);
    }

    private void registerHealthMetrics(MeterRegistry meters) {
        sources.forEach((nodeId, source) -> {
            if (source.address().isBlank()) {
                return;
            }
            Gauge.builder("pilab.sensor.websocket.connected", source,
                            node -> sessions.currentAt(node.address()).isPresent() ? 1.0 : 0.0)
                    .tag("node", nodeId)
                    .description("Whether Spring observes an open WebSocket session for the configured sensor")
                    .register(meters);
            Gauge.builder("pilab.sensor.last.sample.timestamp", source,
                            node -> node.lastSampleMillis().get() / 1000.0)
                    .tag("node", nodeId)
                    .baseUnit("seconds")
                    .description("Receive timestamp of the last accepted sensor sample, or zero before any sample")
                    .register(meters);
        });
    }

    boolean handleIfSample(JsonNode message, WebSocketSession session) throws IOException {
        if (message == null || !"SensorSample".equals(message.path("type").textValue())) {
            return false;
        }

        Instant receivedAt = Instant.now();
        String nodeId = message.path("nodeId").asText("");
        var source = sources.get(nodeId);
        if (source == null || !sessions.isCurrentAt(source.address(), session)) {
            return DeviceMessageReceiver.reject(session, "Unexpected sensor session");
        }

        String runId = message.path("runId").textValue();
        if (runId == null || !RUN_ID.matcher(runId).matches()) {
            return DeviceMessageReceiver.reject(session, "Invalid SensorSample runId");
        }
        var payload = message.path("payload");
        var sequence = payload.path("seq");
        if (!sequence.isIntegralNumber()) {
            return DeviceMessageReceiver.reject(session, "Invalid SensorSample sequence");
        }
        var seq = sequence.bigIntegerValue();
        if (seq.signum() <= 0 || seq.bitLength() > Long.SIZE) {
            return DeviceMessageReceiver.reject(session, "Invalid SensorSample sequence");
        }

        var values = payload.path("values");
        if (!values.isObject() || values.isEmpty()) {
            return DeviceMessageReceiver.reject(session, "Invalid SensorSample values");
        }
        var readings = new HashMap<String, Number>();
        for (var fields = values.fields(); fields.hasNext();) {
            var entry = fields.next();
            String field = entry.getKey();
            var value = entry.getValue();
            double number = value.doubleValue();
            // Keep accepted readings within the range supported by the two-decimal snapshot.
            if (!source.fields().contains(field) || !value.isNumber()
                    || !(Math.abs(number) < Long.MAX_VALUE / 100.0)
                    || ("humidity".equals(field) && (number < 0.0 || number > 100.0))
                    || ("light".equals(field) && number < 0.0)) {
                return DeviceMessageReceiver.reject(session, "Invalid SensorSample values");
            }
            readings.put(field, value.numberValue());
        }

        boolean accepted;
        try {
            accepted = ingestion.accept(new SensorSample(nodeId, runId, seq, receivedAt, readings));
        } catch (SQLException error) {
            LOG.log(Level.WARNING, "sensor_storage_failed nodeId=" + nodeId + " runId=" + runId + " seq=" + seq,
                    error);
            return DeviceMessageReceiver.reject(session, "Sensor sample storage failed");
        }
        if (accepted) {
            // A callback from a replaced session can finish reporting after a newer callback.
            source.lastSampleMillis().accumulateAndGet(receivedAt.toEpochMilli(), Math::max);
            var record = mapper.createObjectNode()
                    .put("event", "sensor_sample")
                    .put("nodeId", nodeId)
                    .put("runId", runId)
                    .put("seq", seq)
                    .put("receivedAt", receivedAt.toString());
            record.set("values", values);
            LOG.info(record::toString);
            if ("airpi".equals(nodeId) && readings.containsKey("dust")) {
                airDust.sample(session, readings.get("dust").doubleValue());
            } else if ("firepi".equals(nodeId) && readings.containsKey("infra") && readings.containsKey("gas")) {
                fireControl.sample(session, readings.get("infra").doubleValue(), readings.get("gas").doubleValue());
            } else if ("lightpi".equals(nodeId) && readings.containsKey("light")) {
                blindAuto.sample(session, readings.get("light").doubleValue());
            }
        }
        return true;
    }

    private record Source(String address, Set<String> fields, AtomicLong lastSampleMillis) {
        Source(String address, Set<String> fields) {
            this(address, fields, new AtomicLong());
        }
    }
}
