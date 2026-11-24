package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.IntNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.socket.WebSocketSession;

import java.util.concurrent.CompletableFuture;

@Component
final class AirServoCommands {
    record Receipt(String commandId, int angle, String status) {}

    private final OutputCommandChannel channel;

    AirServoCommands(ObjectMapper mapper, DeviceSessions sessions, @Value("${PILAB_AIR_IP:}") String address) {
        channel = new OutputCommandChannel(mapper, sessions, address,
                new OutputCommandChannel.Protocol("airpi", "ServoCommand", "angle"));
    }

    CompletableFuture<Receipt> send(int angle) {
        return channel.send(target(angle)).thenApply(AirServoCommands::receipt);
    }

    CompletableFuture<Receipt> send(int angle, WebSocketSession session) {
        return channel.send(target(angle), session).thenApply(AirServoCommands::receipt);
    }

    private static IntNode target(int angle) {
        if (angle < 0 || angle > 180) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Servo angle outside 0..180 degrees");
        }
        return IntNode.valueOf(angle);
    }

    private static Receipt receipt(OutputCommandChannel.Receipt response) {
        return new Receipt(response.commandId(), response.target().intValue(), response.status());
    }

    boolean handle(JsonNode message, WebSocketSession session) {
        return channel.handle(message, session);
    }

    void disconnected(WebSocketSession session) {
        channel.disconnected(session);
    }
}
