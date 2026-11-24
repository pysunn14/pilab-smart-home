package project.smarthome.control;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Path;
import java.util.Optional;
import java.util.function.Function;
import java.util.function.Predicate;
import java.util.logging.Level;
import java.util.logging.Logger;

/** Serializes light decisions and publishes them only after the configured checkpoint is saved. */
public final class ControlService {
    private static final Logger LOG = Logger.getLogger(ControlService.class.getName());
    private final int lightQueueCapacity;
    private final LightQueueFile checkpoint;
    private LightControl light;
    private boolean checkpointFailed;

    public ControlService(int lightQueueCapacity) {
        this(lightQueueCapacity, null);
    }

    public ControlService(int lightQueueCapacity, Path stateFile, ObjectMapper mapper) throws IOException {
        this(lightQueueCapacity, new LightQueueFile(stateFile, mapper));
        light = checkpoint.load(lightQueueCapacity).orElse(null);
    }

    private ControlService(int lightQueueCapacity, LightQueueFile checkpoint) {
        if (lightQueueCapacity <= 0) {
            throw new IllegalArgumentException("The light command queue needs a positive capacity.");
        }
        this.lightQueueCapacity = lightQueueCapacity;
        this.checkpoint = checkpoint;
    }

    /** A repeated startup report must not reset the target of commands already queued. */
    public synchronized boolean initializeLightState(boolean observedOn) {
        checkCheckpoint();
        if (light != null) {
            return false;
        }
        commit(new LightControl(observedOn, lightQueueCapacity));
        return true;
    }

    public synchronized LightControl.Admission acceptButtonPress(ButtonEventId eventId) {
        return change(next -> next.acceptPress(eventId), value -> value == LightControl.Admission.ACCEPTED,
                LightControl.Admission.CONTROL_UNAVAILABLE);
    }

    public synchronized Optional<LightControl.Command> reserveLightCommand(String runId) {
        return change(next -> next.reserveCommand(runId), Optional::isPresent, Optional.empty());
    }

    /** Read the reserved head after transport loss without advancing the queue. */
    public synchronized Optional<LightControl.InFlight> inFlightLightCommand() {
        return light == null ? Optional.empty() : light.inFlightCommand();
    }

    public synchronized boolean confirmedLightOutputMatches(boolean observedOn) {
        return light != null && light.confirmedOutputMatches(observedOn);
    }

    public synchronized boolean claimLightRecoveryReplay(String lastAppliedId, boolean on) {
        return change(next -> next.claimRecoveryReplay(lastAppliedId, on), Boolean::booleanValue, false);
    }

    public synchronized boolean confirmLightApplied(String commandId, boolean on) {
        return change(next -> next.confirmApplied(commandId, on), Boolean::booleanValue, false);
    }

    public synchronized Optional<LightControl.Snapshot> lightSnapshot() {
        return light == null ? Optional.empty() : Optional.of(light.snapshot());
    }

    private <T> T change(Function<LightControl, T> update, Predicate<T> changed, T unavailable) {
        checkCheckpoint();
        if (light == null) {
            return unavailable;
        }
        if (checkpoint == null) {
            return update.apply(light);
        }
        var next = light.copy();
        var result = update.apply(next);
        if (changed.test(result)) {
            commit(next);
        }
        return result;
    }

    private void checkCheckpoint() {
        if (checkpointFailed) {
            throw new IllegalStateException("Light control is stopped after a checkpoint failure.");
        }
    }

    private void commit(LightControl next) {
        if (checkpoint != null) {
            try {
                checkpoint.save(next.checkpoint());
            } catch (IOException error) {
                // A failure after replacement can leave the file ahead of memory. Stop serving new decisions.
                checkpointFailed = true;
                LOG.log(Level.SEVERE, "light_queue_checkpoint_failed", error);
                throw new UncheckedIOException("Could not save the light queue checkpoint.", error);
            }
        }
        light = next;
    }
}
