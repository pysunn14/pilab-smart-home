package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.BooleanNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

import java.util.concurrent.CompletableFuture;

@Component
final class FireAlarmCommands {
    record Receipt(String commandId, boolean on, String status) {}

    private final OutputCommandChannel channel;

    FireAlarmCommands(ObjectMapper mapper, DeviceSessions sessions, @Value("${PILAB_FIRE_IP:}") String address) {
        channel = new OutputCommandChannel(mapper, sessions, address,
                new OutputCommandChannel.Protocol("firepi", "FireAlarmCommand", "on"));
    }

    CompletableFuture<Receipt> send(boolean on) {
        return channel.send(BooleanNode.valueOf(on)).thenApply(FireAlarmCommands::receipt);
    }

    CompletableFuture<Receipt> send(boolean on, WebSocketSession session) {
        return channel.send(BooleanNode.valueOf(on), session).thenApply(FireAlarmCommands::receipt);
    }

    private static Receipt receipt(OutputCommandChannel.Receipt result) {
        return new Receipt(result.commandId(), result.target().booleanValue(), result.status());
    }

    boolean handle(JsonNode message, WebSocketSession session) {
        return channel.handle(message, session);
    }

    void disconnected(WebSocketSession session) {
        channel.disconnected(session);
    }
}
