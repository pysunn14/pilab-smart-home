package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

@Component
final class FireAlarmControl {
    // These thresholds reproduce the FirePi inputs in the original virtual scenario.
    private static final double INFRA_LIMIT = 800.0;
    private static final double GAS_LIMIT = 400.0;

    private final boolean enabled;
    private final AutomaticOutput<Boolean> output;

    FireAlarmControl(FireAlarmCommands commands, DeviceSessions sessions,
                     @Value("${PILAB_FIRE_IP:}") String address,
                     @Value("${PILAB_FIRE_AUTO_ALARM:false}") boolean enabled) {
        this.enabled = enabled;
        output = new AutomaticOutput<>(sessions, address, "fire_alarm", "on",
                (on, session) -> commands.send(on, session)
                        .thenApply(reply -> new AutomaticOutput.Result(reply.commandId(), reply.status())));
    }

    boolean enabled() {
        return enabled;
    }

    void sample(WebSocketSession source, double infra, double gas) {
        if (enabled) {
            output.target(source, alarmFor(infra, gas));
        }
    }

    static boolean alarmFor(double infra, double gas) {
        return infra <= INFRA_LIMIT || gas >= GAS_LIMIT;
    }

    boolean report(JsonNode message, WebSocketSession source) {
        if (message == null || !"FireAlarmStateReported".equals(message.path("type").textValue())) {
            return false;
        }
        var on = message.path("payload").path("on");
        if (enabled && "firepi".equals(message.path("nodeId").textValue()) && on.isBoolean()) {
            output.report(source, on.booleanValue());
        }
        return true;
    }

    void disconnected(WebSocketSession source) {
        output.disconnected(source);
    }

    @PreDestroy
    void close() {
        output.close();
    }
}
