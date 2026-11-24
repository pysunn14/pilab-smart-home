package project.smarthome.control;

/** The sequence carries the bits of MainPi's unsigned 64-bit counter. */
public record ButtonEventId(String nodeId, String runId, long sequence) {
    public ButtonEventId {
        if (nodeId == null || nodeId.isBlank() || runId == null || runId.isBlank() || sequence == 0) {
            throw new IllegalArgumentException("A button event requires a node ID, run ID, and nonzero sequence.");
        }
    }
}
