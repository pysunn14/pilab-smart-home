package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

@Component
final class AirDustControl {
    // The original smart-home rule supplies the cutoff for the virtual-room scenario.
    private static final double DUST_LIMIT = 100.0;

    private final boolean enabled;
    private final AutomaticOutput<Integer> output;

    AirDustControl(AirServoCommands servo, DeviceSessions sessions, @Value("${PILAB_AIR_IP:}") String airAddress,
                   @Value("${PILAB_AIR_AUTO_SERVO:false}") boolean enabled) {
        this.enabled = enabled;
        output = new AutomaticOutput<>(sessions, airAddress, "air_servo", "angle",
                (angle, session) -> servo.send(angle, session)
                        .thenApply(reply -> new AutomaticOutput.Result(reply.commandId(), reply.status())));
    }

    boolean enabled() {
        return enabled;
    }

    void sample(WebSocketSession source, double dust) {
        if (enabled) {
            output.target(source, dust > DUST_LIMIT ? 0 : 90);
        }
    }

    boolean report(JsonNode message, WebSocketSession source) {
        if (message == null || !"ServoStateReported".equals(message.path("type").textValue())) {
            return false;
        }
        var angle = message.path("payload").path("angle");
        if (enabled && "airpi".equals(message.path("nodeId").textValue()) && angle.isIntegralNumber()
                && angle.canConvertToInt() && angle.intValue() >= 0 && angle.intValue() <= 180) {
            output.report(source, angle.intValue());
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
