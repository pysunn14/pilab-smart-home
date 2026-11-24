package project.smarthome.telemetry;

import org.springframework.stereotype.Component;

import java.util.HashMap;
import java.util.Map;

@Component
public class SensorSnapshot {
    private Map<String, Double> values = Map.of(
            "temperature", 20.0, "humidity", 30.0, "gas", 10.0,
            "light", 400.0, "dust", 10.0, "infra", 1000.0);

    public synchronized Map<String, Double> read() {
        return values;
    }

    public synchronized void update(Map<String, Number> readings) {
        var next = new HashMap<>(values);
        for (var entry : readings.entrySet()) {
            if (next.containsKey(entry.getKey())) {
                double value = entry.getValue().doubleValue();
                next.put(entry.getKey(), Math.round(value * 100.0) / 100.0);
            }
        }
        values = Map.copyOf(next);
    }
}
