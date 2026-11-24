package project.smarthome.handler;

import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketSession;

import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

@Component
public class DeviceSessions {
    private final ConcurrentMap<String, WebSocketSession> byAddress = new ConcurrentHashMap<>();

    public void register(WebSocketSession session) {
        String address = addressOf(session);
        if (address != null) {
            byAddress.put(address, session);
        }
    }

    public void unregister(WebSocketSession session) {
        String address = addressOf(session);
        if (address != null) {
            // The same address can already belong to a newer connection.
            byAddress.remove(address, session);
        }
    }

    boolean isCurrentAt(String address, WebSocketSession session) {
        return byAddress.get(address) == session && session.isOpen();
    }

    Optional<WebSocketSession> currentAt(String address) {
        return Optional.ofNullable(byAddress.get(address)).filter(WebSocketSession::isOpen);
    }

    public Map<String, String> activeIdentities() {
        Map<String, String> active = new TreeMap<>();
        byAddress.forEach((address, session) -> {
            if (session.isOpen()) {
                active.put(address, session.getId());
            }
        });
        return active;
    }

    private String addressOf(WebSocketSession session) {
        var remote = session.getRemoteAddress();
        return remote == null || remote.getAddress() == null ? null : remote.getAddress().getHostAddress();
    }
}
