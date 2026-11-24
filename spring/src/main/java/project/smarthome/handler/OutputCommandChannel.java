package project.smarthome.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import java.io.IOException;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** One in-flight command on one actuator's current WebSocket session. */
final class OutputCommandChannel {
    record Protocol(String nodeId, String commandType, String field) {}
    record Receipt(String commandId, JsonNode target, String status) {}

    private record Pending(WebSocketSession session, String id, JsonNode target, CompletableFuture<Receipt> result) {
        Receipt receipt(String status) {
            return new Receipt(id, target, status);
        }
    }

    private final ObjectMapper mapper;
    private final DeviceSessions sessions;
    private final String address;
    private final Protocol protocol;
    private final AtomicReference<Pending> pending = new AtomicReference<>();

    OutputCommandChannel(ObjectMapper mapper, DeviceSessions sessions, String address, Protocol protocol) {
        this.mapper = mapper;
        this.sessions = sessions;
        this.address = address;
        this.protocol = protocol;
    }

    CompletableFuture<Receipt> send(JsonNode target) {
        return send(target, sessions.currentAt(address).orElse(null));
    }

    CompletableFuture<Receipt> send(JsonNode target, WebSocketSession session) {
        if (session == null || !sessions.isCurrentAt(address, session)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, protocol.nodeId() + " is disconnected");
        }
        var command = new Pending(session, UUID.randomUUID().toString(), target, new CompletableFuture<>());
        if (!pending.compareAndSet(null, command)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "An output command is awaiting confirmation");
        }
        var result = command.result().whenComplete((receipt, failure) -> pending.compareAndSet(command, null));
        var message = mapper.createObjectNode().put("type", protocol.commandType()).put("nodeId", protocol.nodeId());
        message.putObject("payload").put("commandId", command.id()).set(protocol.field(), target);
        try {
            // Share the send lock with protocol error replies on the same session.
            synchronized (session) {
                if (!command.result().isDone() && sessions.isCurrentAt(address, session)) {
                    session.sendMessage(new TextMessage(mapper.writeValueAsString(message)));
                } else {
                    command.result().complete(command.receipt("unconfirmed"));
                }
            }
        } catch (IOException | IllegalStateException error) {
            command.result().complete(command.receipt("unconfirmed"));
        }
        command.result().completeOnTimeout(command.receipt("unconfirmed"), 3, TimeUnit.SECONDS);
        return result;
    }

    boolean handle(JsonNode message, WebSocketSession session) {
        if (message == null) {
            return false;
        }
        String type = message.path("type").textValue();
        boolean applied = (protocol.commandType() + "Applied").equals(type);
        if (!applied && !(protocol.commandType() + "Unconfirmed").equals(type)) {
            return false;
        }
        var command = pending.get();
        var payload = message.path("payload");
        if (command != null && command.session() == session && sessions.isCurrentAt(address, session)
                && protocol.nodeId().equals(message.path("nodeId").textValue())
                && command.id().equals(payload.path("commandId").textValue())
                && matches(command.target(), payload.path(protocol.field()))) {
            command.result().complete(command.receipt(applied ? "applied" : "unconfirmed"));
        }
        return true;
    }

    private static boolean matches(JsonNode target, JsonNode actual) {
        return target.isIntegralNumber()
                ? actual.isIntegralNumber() && actual.canConvertToInt() && target.intValue() == actual.intValue()
                : target.equals(actual);
    }

    void disconnected(WebSocketSession session) {
        var command = pending.get();
        if (command != null && command.session() == session) {
            command.result().complete(command.receipt("unconfirmed"));
        }
    }
}
