package project.smarthome.handler;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.socket.WebSocketSession;

import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.logging.Level;
import java.util.logging.Logger;

@Component
final class BlindAutoControl {
    private static final Logger LOG = Logger.getLogger(BlindAutoControl.class.getName());
    // These are the original Spring controller's exclusive cutoffs.
    private static final double DARK_LIMIT = 200.0;
    private static final double BRIGHT_LIMIT = 750.0;

    private record Dispatch(WebSocketSession session, BlindCommands.Direction direction) {}

    private final BlindCommands commands;
    private final DeviceSessions sessions;
    private final String address;
    private final boolean enabled;
    private final Executor writer;

    // A neutral sample clears the latch without writing to the motor.
    private BlindCommands.Direction latched;
    private Dispatch queued;
    private boolean sending;

    BlindAutoControl(BlindCommands commands, DeviceSessions sessions, @Value("${PILAB_LIGHT_IP:}") String address,
                     @Value("${PILAB_LIGHT_AUTO_BLIND:false}") boolean enabled,
                     @Qualifier("applicationTaskExecutor") Executor writer) {
        this.commands = commands;
        this.sessions = sessions;
        this.address = address;
        this.enabled = enabled;
        this.writer = writer;
    }

    boolean enabled() {
        return enabled;
    }

    synchronized void sample(WebSocketSession source, double light) {
        if (!enabled || !sessions.isCurrentAt(address, source)) {
            return;
        }
        if (light >= DARK_LIMIT && light <= BRIGHT_LIMIT) {
            if (latched != null) {
                latched = null;
                queued = null;
                LOG.info(() -> "blind_auto_state state=IDLE light=" + light);
            }
        } else if (latched == null) {
            var direction = light < DARK_LIMIT ? BlindCommands.Direction.UP : BlindCommands.Direction.DOWN;
            latched = direction;
            queued = new Dispatch(source, direction);
            LOG.info(() -> "blind_auto_state state=" + direction + " light=" + light);
            schedule();
        }
    }

    // The caller holds the control lock. Socket writes run on the existing application executor.
    private void schedule() {
        if (sending || queued == null) {
            return;
        }
        sending = true;
        try {
            writer.execute(this::sendNext);
        } catch (RejectedExecutionException error) {
            LOG.log(Level.WARNING, "blind_auto_not_sent direction=" + queued.direction(), error);
            sending = false;
            queued = null;
        }
    }

    private void sendNext() {
        Dispatch dispatch;
        synchronized (this) {
            dispatch = queued;
            queued = null;
            if (dispatch == null) {
                sending = false;
                return;
            }
        }
        try {
            commands.send(dispatch.direction(), dispatch.session())
                    .whenComplete((receipt, error) -> finish(dispatch, receipt, error));
        } catch (ResponseStatusException error) {
            finish(dispatch, null, error);
        }
    }

    private synchronized void finish(Dispatch dispatch, BlindCommands.Receipt receipt, Throwable error) {
        if (error != null) {
            LOG.log(Level.WARNING, "blind_auto_not_confirmed direction=" + dispatch.direction(), error);
        } else {
            LOG.info(() -> "blind_auto_result commandId=" + receipt.commandId()
                    + " direction=" + receipt.direction() + " status=" + receipt.status());
        }
        sending = false;
        schedule();
    }
}
