package project.smarthome.telemetry;

import org.springframework.stereotype.Component;

import java.math.BigInteger;
import java.sql.SQLException;
import java.util.HashMap;
import java.util.Map;
import java.util.Optional;

@Component
public class SensorIngestion {
    private final SensorSnapshot snapshot;
    private final Optional<SqliteSensorHistory> history;
    private final Map<SensorRun, BigInteger> lastSequences = new HashMap<>();

    public SensorIngestion(SensorSnapshot snapshot, Optional<SqliteSensorHistory> history) {
        this.snapshot = snapshot;
        this.history = history;
    }

    public synchronized boolean accept(SensorSample sample) throws SQLException {
        if (history.isPresent()) {
            if (!history.get().appendIfNew(sample)) {
                return false;
            }
        } else {
            var run = new SensorRun(sample.nodeId(), sample.runId());
            var lastSequence = lastSequences.get(run);
            if (lastSequence != null && sample.sequence().compareTo(lastSequence) <= 0) {
                return false;
            }
            lastSequences.put(run, sample.sequence());
        }
        // Serialize typed admission through publication without holding the snapshot lock during disk I/O.
        snapshot.update(sample.values());
        return true;
    }

    private record SensorRun(String nodeId, String runId) {
    }
}
