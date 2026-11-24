package project.smarthome.telemetry;

import java.math.BigInteger;
import java.time.Instant;
import java.util.Map;

public record SensorSample(String nodeId, String runId, BigInteger sequence, Instant receivedAt,
                           Map<String, Number> values) {
    public SensorSample {
        values = Map.copyOf(values);
    }
}
