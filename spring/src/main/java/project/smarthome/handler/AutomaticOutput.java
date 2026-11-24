package project.smarthome.handler;

import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.socket.WebSocketSession;

import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.BiFunction;
import java.util.logging.Logger;

/** Reconcile the latest target with the reported output of the current device session. */
final class AutomaticOutput<T> {
    private static final Logger LOG = Logger.getLogger(AutomaticOutput.class.getName());

    record Result(String commandId, String status) {}

    private final DeviceSessions sessions;
    private final String address;
    private final String name;
    private final String field;
    private final BiFunction<T, WebSocketSession, CompletableFuture<Result>> send;
    private final ExecutorService writer;

    private WebSocketSession session;
    private T desired;
    private T observed;
    private boolean reported;
    private boolean attempted;
    private boolean dispatching;
    private boolean closed;

    AutomaticOutput(DeviceSessions sessions, String address, String name, String field,
                    BiFunction<T, WebSocketSession, CompletableFuture<Result>> send) {
        this.sessions = sessions;
        this.address = address;
        this.name = name;
        this.field = field;
        this.send = send;
        writer = Executors.newSingleThreadExecutor(task -> new Thread(task, name + "-writer"));
    }

    synchronized void target(WebSocketSession source, T value) {
        if (!sessions.isCurrentAt(address, source)) {
            return;
        }
        select(source);
        if (!Objects.equals(desired, value)) {
            desired = value;
            attempted = false;
        }
        schedule();
    }

    synchronized void report(WebSocketSession source, T value) {
        if (!sessions.isCurrentAt(address, source)) {
            return;
        }
        select(source);
        if (!reported) {
            reported = true;
            observed = value;
            schedule();
        }
    }

    synchronized void disconnected(WebSocketSession source) {
        if (session == source) {
            select(null);
        }
    }

    private void select(WebSocketSession source) {
        if (session != source) {
            session = source;
            desired = null;
            observed = null;
            reported = false;
            attempted = false;
        }
    }

    private boolean hasWork() {
        return !closed && reported && desired != null && !attempted && !desired.equals(observed)
                && sessions.isCurrentAt(address, session);
    }

    private void schedule() {
        if (!dispatching && hasWork()) {
            dispatching = true;
            writer.execute(this::sendLatest);
        }
    }

    private void sendLatest() {
        WebSocketSession targetSession;
        T value;
        synchronized (this) {
            if (!hasWork()) {
                dispatching = false;
                return;
            }
            targetSession = session;
            value = desired;
            attempted = true;
        }
        try {
            send.apply(value, targetSession).whenComplete((receipt, error) -> finish(targetSession, value, receipt));
        } catch (ResponseStatusException error) {
            LOG.info(() -> name + "_auto_not_sent " + field + "=" + value + " reason=" + error.getReason());
            finish(targetSession, value, null);
        }
    }

    private synchronized void finish(WebSocketSession targetSession, T value, Result receipt) {
        if (receipt != null) {
            LOG.info(() -> name + "_auto_result commandId=" + receipt.commandId()
                    + " " + field + "=" + value + " status=" + receipt.status());
            if (session == targetSession) {
                observed = "applied".equals(receipt.status()) ? value : null;
            }
        }
        dispatching = false;
        schedule();
    }

    synchronized void close() {
        closed = true;
        writer.shutdownNow();
    }
}
