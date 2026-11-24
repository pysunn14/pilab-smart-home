package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import project.smarthome.control.ButtonEventId;
import project.smarthome.control.LightControl;

import java.io.IOException;
import java.util.logging.Logger;
import java.util.regex.Pattern;

final class DeviceMessageReceiver {
    private static final Logger LOG = Logger.getLogger(DeviceMessageReceiver.class.getName());
    private static final Pattern RUN_ID = Pattern.compile("[0-9a-f]{32}");

    private DeviceMessageReceiver() {
    }

    /**
     * Handles typed device messages. Returning false routes a flat numeric
     * message to the sensor handler used by the other Pi nodes.
     */
    static boolean handle(JsonNode message, WebSocketSession session, LightCommandSender lightCommands)
            throws IOException {
        long receivedNs = System.nanoTime();
        if (message == null || !message.isObject()) {
            return reject(session, "Invalid JSON object");
        }
        if (!message.has("type")) {
            return false;
        }

        JsonNode type = message.path("type");
        JsonNode nodeId = message.path("nodeId");
        JsonNode payload = message.path("payload");
        if (!type.isTextual() || !nodeId.isTextual() || nodeId.textValue().isBlank()
                || !payload.isObject()) {
            return reject(session, "Invalid device message");
        }
        boolean initialReport = "LightStateReported".equals(type.textValue());
        boolean snapshot = "LightStateSnapshot".equals(type.textValue());
        if (initialReport || snapshot || "LightCommandApplied".equals(type.textValue())) {
            if (!lightCommands.isCurrentLightSession(session) || !"lightpi".equals(nodeId.textValue())) {
                return reject(session, "Unexpected LightPi session");
            }
            if (snapshot) {
                return handleLightStateSnapshot(payload, session, lightCommands);
            }
            return initialReport ? handleLightStateReport(message.path("runId"), payload, session, lightCommands)
                    : handleLightCommandApplied(payload, session, lightCommands);
        }
        if (!"ButtonPressed".equals(type.textValue())) {
            return reject(session, "Unsupported device message type");
        }

        JsonNode runId = message.path("runId");
        if (!runId.isTextual() || !RUN_ID.matcher(runId.textValue()).matches()) {
            return reject(session, "Invalid ButtonPressed runId");
        }

        JsonNode sequence = payload.path("seq");
        if (!sequence.isIntegralNumber() || sequence.bigIntegerValue().signum() <= 0
                || sequence.bigIntegerValue().bitLength() > Long.SIZE) {
            return reject(session, "Invalid ButtonPressed sequence");
        }

        var eventId = new ButtonEventId(nodeId.textValue(), runId.textValue(), sequence.longValue());
        String identity = "nodeId=" + nodeId + " runId=" + eventId.runId()
                + " seq=" + Long.toUnsignedString(eventId.sequence());
        LOG.info(() -> "received_ns=" + receivedNs + " button_received " + identity);

        var admission = lightCommands.acceptButtonPress(eventId);
        LOG.info(() -> "button_admission " + identity + " result=" + admission);
        if (admission == LightControl.Admission.ACCEPTED) {
            lightCommands.dispatch();
        }
        return true;
    }

    private static boolean handleLightStateReport(JsonNode runId, JsonNode payload, WebSocketSession session,
                                                  LightCommandSender lightCommands) throws IOException {
        JsonNode on = payload.path("on");
        if (!on.isBoolean()) {
            return reject(session, "Invalid LightStateReported on value");
        }
        if (!runId.isTextual() || !RUN_ID.matcher(runId.textValue()).matches()) {
            return reject(session, "Invalid LightStateReported runId");
        }
        boolean observedOn = on.booleanValue();
        String result = lightCommands.initialize(session, runId.textValue(), observedOn);
        LOG.info(() -> "light_state_reported on=" + observedOn + " result=" + result);
        return true;
    }

    private static boolean handleLightCommandApplied(JsonNode payload, WebSocketSession session,
                                                     LightCommandSender lightCommands) throws IOException {
        JsonNode commandId = payload.path("commandId");
        JsonNode on = payload.path("on");
        if (!commandId.isTextual() || commandId.textValue().isBlank() || !on.isBoolean()) {
            return reject(session, "Invalid LightCommandApplied payload");
        }
        boolean applied = lightCommands.confirmApplied(session, commandId.textValue(), on.booleanValue());
        LOG.info(() -> "light_command_applied commandId=" + commandId + " on=" + on
                + " result=" + (applied ? "CONFIRMED" : "NOT_PENDING"));
        if (applied) {
            lightCommands.dispatch();
        }
        return true;
    }

    private static boolean handleLightStateSnapshot(JsonNode payload, WebSocketSession session,
                                                     LightCommandSender lightCommands) throws IOException {
        var queryId = payload.path("queryId");
        var lastApplied = payload.path("lastAppliedCommandId");
        var on = payload.path("on");
        if (!queryId.isTextual() || queryId.textValue().isBlank() || !on.isBoolean()
                || !(lastApplied.isNull() || lastApplied.isTextual() && !lastApplied.textValue().isBlank())) {
            return reject(session, "Invalid LightStateSnapshot payload");
        }
        String result = lightCommands.reconcileState(session, queryId.textValue(), lastApplied.textValue(),
                on.booleanValue());
        LOG.info(() -> "light_state_snapshot queryId=" + queryId + " lastAppliedCommandId=" + lastApplied
                + " on=" + on + " result=" + result);
        if ("RECONCILED".equals(result)) {
            lightCommands.dispatch();
        }
        return true;
    }

    static boolean reject(WebSocketSession session, String error) throws IOException {
        // LightCommandSender also locks the session for command frames.
        synchronized (session) {
            session.sendMessage(new TextMessage("{\"status\":\"error\",\"message\":\"" + error + "\"}"));
        }
        return true;
    }
}
