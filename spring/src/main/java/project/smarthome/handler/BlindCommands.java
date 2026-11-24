package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.TextNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

import java.util.concurrent.CompletableFuture;

@Component
final class BlindCommands {
    enum Direction { UP, DOWN }

    record Receipt(String commandId, Direction direction, String status) {}

    private final OutputCommandChannel channel;

    BlindCommands(ObjectMapper mapper, DeviceSessions sessions, @Value("${PILAB_LIGHT_IP:}") String address) {
        channel = new OutputCommandChannel(mapper, sessions, address,
                new OutputCommandChannel.Protocol("lightpi", "BlindCommand", "direction"));
    }

    CompletableFuture<Receipt> send(Direction direction) {
        return channel.send(TextNode.valueOf(direction.name())).thenApply(BlindCommands::receipt);
    }

    CompletableFuture<Receipt> send(Direction direction, WebSocketSession session) {
        return channel.send(TextNode.valueOf(direction.name()), session).thenApply(BlindCommands::receipt);
    }

    private static Receipt receipt(OutputCommandChannel.Receipt response) {
        return new Receipt(response.commandId(), Direction.valueOf(response.target().textValue()), response.status());
    }

    boolean handle(JsonNode message, WebSocketSession session) {
        return channel.handle(message, session);
    }

    void disconnected(WebSocketSession session) {
        channel.disconnected(session);
    }
}
