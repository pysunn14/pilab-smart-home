package project.smarthome.handler;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import project.smarthome.control.ButtonEventId;
import project.smarthome.control.ControlService;
import project.smarthome.control.LightControl;

import java.io.IOException;
import java.util.Optional;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.logging.Logger;

@Component
final class LightCommandSender {
    private static final Logger LOG = Logger.getLogger(LightCommandSender.class.getName());
    private final ObjectMapper mapper;
    private final DeviceSessions sessions;
    private final Optional<ControlService> control;
    private final String lightAddress;
    private final long ackTimeoutMs;
    private final ScheduledThreadPoolExecutor writer;
    private WebSocketSession lightSession;
    private String lightRunId;
    private Pending pending;
    private boolean dispatchScheduled;
    private boolean stopped;

    private enum Phase { AWAITING_ACK, SUSPENDED, QUERYING, QUERY_FAILED }

    private static final class Pending {
        final WebSocketSession session;
        final LightControl.Command command;
        final boolean sameRunReconnect;
        boolean retried;
        ScheduledFuture<?> timeout;
        Phase phase = Phase.AWAITING_ACK;

        Pending(WebSocketSession session, LightControl.Command command, boolean sameRunReconnect) {
            this.session = session;
            this.command = command;
            this.sameRunReconnect = sameRunReconnect;
        }
    }

    LightCommandSender(ObjectMapper mapper, DeviceSessions sessions, Optional<ControlService> control,
                       @Value("${PILAB_LIGHT_IP:}") String lightAddress,
                       @Value("${pilab.control.light.ack-timeout-ms:0}") long ackTimeoutMs) {
        if (ackTimeoutMs < 0) {
            throw new IllegalArgumentException("Light ACK timeout must be nonnegative.");
        }
        this.mapper = mapper;
        this.sessions = sessions;
        this.control = control;
        this.lightAddress = lightAddress;
        this.ackTimeoutMs = ackTimeoutMs;
        this.writer = new ScheduledThreadPoolExecutor(1, task -> new Thread(task, "light-command-sender"));
        this.writer.setRemoveOnCancelPolicy(true);
    }

    boolean isCurrentLightSession(WebSocketSession session) {
        return sessions.isCurrentAt(lightAddress, session);
    }

    LightControl.Admission acceptButtonPress(ButtonEventId eventId) {
        return control.map(service -> service.acceptButtonPress(eventId))
                .orElse(LightControl.Admission.CONTROL_UNAVAILABLE);
    }

    synchronized String initialize(WebSocketSession session, String runId, boolean observedOn) {
        if (stopped || control.isEmpty()) {
            return LightControl.Admission.CONTROL_UNAVAILABLE.name();
        }
        if (!isCurrentLightSession(session)) {
            return "STALE_SESSION";
        }
        var controller = control.orElseThrow();
        if (controller.initializeLightState(observedOn)) {
            lightSession = session;
            lightRunId = runId;
            return "INITIALIZED";
        }
        if (lightSession != null && lightSession.isOpen()) {
            return "ALREADY_INITIALIZED";
        }
        var unconfirmed = controller.inFlightLightCommand();
        if (unconfirmed.isPresent()) {
            // The startup GPIO value cannot identify the command that set it.
            clearPending();
            lightSession = session;
            lightRunId = runId;
            var reservation = unconfirmed.orElseThrow();
            pending = new Pending(session, reservation.command(), runId.equals(reservation.runId()));
            startStateQuery(pending);
            return "RECOVERING";
        }
        // Queued commands without a reservation have never been sent.
        if (!controller.confirmedLightOutputMatches(observedOn)) {
            return "STATE_MISMATCH";
        }
        lightSession = session;
        lightRunId = runId;
        dispatch();
        return "RECONNECTED";
    }

    synchronized boolean confirmApplied(WebSocketSession session, String commandId, boolean on) {
        if (pending == null || pending.phase != Phase.AWAITING_ACK
                || session != pending.session || !isCurrentLightSession(session)
                || !control.orElseThrow().confirmLightApplied(commandId, on)) {
            return false;
        }
        clearPending();
        return true;
    }

    synchronized boolean requestStateQuery() {
        var attempt = pending;
        if (attempt == null || attempt.phase != Phase.SUSPENDED || !canSend(attempt)) {
            return false;
        }
        startStateQuery(attempt);
        return true;
    }

    private void startStateQuery(Pending attempt) {
        // The command ID identifies the one query on this session.
        attempt.phase = Phase.QUERYING;
        writer.execute(() -> sendStateQuery(attempt));
    }

    synchronized String reconcileState(WebSocketSession session, String queryId, String lastAppliedId, boolean on) {
        if (pending == null || pending.phase != Phase.QUERYING || pending.session != session
                || !isCurrentLightSession(session) || !pending.command.commandId().equals(queryId)) {
            return "NO_MATCHING_QUERY";
        }
        var controller = control.orElseThrow();
        if (lastAppliedId != null && controller.confirmLightApplied(lastAppliedId, on)) {
            clearPending();
            return "RECONCILED";
        }
        var attempt = pending;
        if (attempt.sameRunReconnect && controller.claimLightRecoveryReplay(lastAppliedId, on)) {
            attempt.phase = Phase.AWAITING_ACK;
            attempt.retried = true; // The recovery replay already uses this session's retry.
            writer.execute(() -> send(attempt, true));
            return "REPLAYING";
        }
        holdAfterQueryFailure("STATE_MISMATCH");
        return "MISMATCH";
    }

    synchronized void disconnected(WebSocketSession session) {
        if (session == lightSession) {
            suspend("DISCONNECTED");
        }
    }

    synchronized void dispatch() {
        if (!stopped && lightSession != null && pending == null && !dispatchScheduled) {
            // Coalesce wakeups so the executor holds at most one queued dispatch task.
            dispatchScheduled = true;
            writer.execute(this::sendNext);
        }
    }

    private void sendNext() {
        Pending attempt;
        synchronized (this) {
            dispatchScheduled = false;
            if (stopped || lightSession == null || !isCurrentLightSession(lightSession) || pending != null) {
                return;
            }
            var command = control.orElseThrow().reserveLightCommand(lightRunId);
            if (command.isEmpty()) {
                return;
            }
            // A reply can arrive while sendMessage is still running.
            attempt = new Pending(lightSession, command.get(), false);
            pending = attempt;
        }
        send(attempt, false);
    }

    private void send(Pending attempt, boolean retry) {
        var command = attempt.command;
        var frame = mapper.createObjectNode().put("type", "LightCommand").put("nodeId", "lightpi");
        var payload = frame.putObject("payload").put("commandId", command.commandId()).put("on", command.on());
        var commandId = payload.path("commandId");
        if (!write(attempt, new TextMessage(frame.toString()))) {
            return;
        }
        if (retry) {
            LOG.info(() -> "light_command_retry commandId=" + commandId);
        }
        LOG.info(() -> "light_command_sent commandId=" + commandId + " on=" + command.on());
        synchronized (this) {
            if (ackTimeoutMs > 0 && canSend(attempt)) {
                attempt.timeout = writer.schedule(() -> onTimeout(attempt), ackTimeoutMs, TimeUnit.MILLISECONDS);
            }
        }
    }

    private void sendStateQuery(Pending attempt) {
        var frame = mapper.createObjectNode().put("type", "LightStateQuery").put("nodeId", "lightpi");
        var query = frame.putObject("payload").put("queryId", attempt.command.commandId()).path("queryId");
        if (write(attempt, new TextMessage(frame.toString()))) {
            LOG.info(() -> "light_state_query_sent queryId=" + query);
        }
    }

    private boolean write(Pending attempt, TextMessage message) {
        try {
            synchronized (attempt.session) {
                if (!canSend(attempt)) {
                    return false;
                }
                attempt.session.sendMessage(message);
            }
            return true;
        } catch (IOException | IllegalStateException error) {
            synchronized (this) {
                if (pending == attempt) {
                    String reason = error.getClass().getSimpleName();
                    if (attempt.phase == Phase.QUERYING) {
                        holdAfterQueryFailure(reason);
                    } else {
                        suspend(reason);
                    }
                }
            }
            return false;
        }
    }

    private synchronized boolean canSend(Pending attempt) {
        if (stopped || pending != attempt) {
            return false;
        }
        if (!isCurrentLightSession(attempt.session)) {
            suspend("STALE_SESSION");
            return false;
        }
        return true;
    }

    private void onTimeout(Pending attempt) {
        synchronized (this) {
            if (!canSend(attempt)) {
                return;
            }
            attempt.timeout = null;
            if (attempt.retried) {
                attempt.phase = Phase.SUSPENDED;
                logSuspended("ACK_TIMEOUT");
                return;
            }
            attempt.retried = true;
        }
        send(attempt, true);
    }

    // Caller holds the sender monitor. The core queue keeps the command until reconciliation.
    private void suspend(String reason) {
        logSuspended(reason);
        clearPending();
        lightSession = null;
    }

    private void logSuspended(String reason) {
        if (pending == null) {
            LOG.warning(() -> "light_dispatch_suspended reason=" + reason);
        } else {
            var commandId = mapper.getNodeFactory().textNode(pending.command.commandId());
            LOG.warning(() -> "light_dispatch_suspended reason=" + reason
                    + " commandId=" + commandId + " application=UNKNOWN");
        }
    }

    private void holdAfterQueryFailure(String reason) {
        pending.phase = Phase.QUERY_FAILED;
        logSuspended(reason);
    }

    private void clearPending() {
        if (pending != null && pending.timeout != null) {
            pending.timeout.cancel(false);
        }
        pending = null;
    }

    @PreDestroy
    synchronized void stop() {
        stopped = true;
        clearPending();
        writer.shutdownNow();
    }
}
