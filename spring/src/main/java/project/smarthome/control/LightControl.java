package project.smarthome.control;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

public final class LightControl {
    public enum Admission {
        ACCEPTED,
        DUPLICATE,
        QUEUE_FULL,
        CONTROL_UNAVAILABLE
    }

    public record Command(ButtonEventId eventId, boolean on) {
        public String commandId() {
            return eventId.nodeId() + "/" + eventId.runId() + "/" + Long.toUnsignedString(eventId.sequence());
        }
    }

    public record InFlight(Command command, String runId) {}

    public record Snapshot(boolean targetOn, List<Command> pending) {}

    record Checkpoint(int version, boolean targetOn, List<Command> pending, Set<ButtonEventId> acceptedEvents,
                      String inFlightRunId, String confirmedCommandId, boolean confirmedOn,
                      boolean recoveryReplayClaimed) {}

    private static final int CHECKPOINT_VERSION = 1;

    private final int capacity;
    private final Deque<Command> pending = new ArrayDeque<>();
    private final Set<ButtonEventId> acceptedEvents = new HashSet<>();
    private boolean targetOn;
    private String inFlightRunId;
    private String confirmedCommandId;
    private boolean confirmedOn;
    private boolean recoveryReplayClaimed;

    LightControl(boolean initialOn, int capacity) {
        this.capacity = capacity;
        this.targetOn = initialOn;
        this.confirmedOn = initialOn;
    }

    private LightControl(Checkpoint saved, int capacity) {
        this(saved.confirmedOn(), capacity);
        targetOn = saved.targetOn();
        pending.addAll(saved.pending());
        acceptedEvents.addAll(saved.acceptedEvents());
        inFlightRunId = saved.inFlightRunId();
        confirmedCommandId = saved.confirmedCommandId();
        recoveryReplayClaimed = saved.recoveryReplayClaimed();
    }

    static LightControl restore(Checkpoint saved, int capacity) {
        if (saved == null || saved.version() != CHECKPOINT_VERSION || saved.pending() == null
                || saved.acceptedEvents() == null || saved.pending().size() > capacity
                || (saved.pending().isEmpty() && saved.inFlightRunId() != null)
                || (saved.inFlightRunId() != null && saved.inFlightRunId().isBlank())
                || (saved.recoveryReplayClaimed() && saved.inFlightRunId() == null)) {
            throw new IllegalArgumentException("Invalid light queue checkpoint.");
        }
        boolean target = saved.confirmedOn();
        Set<ButtonEventId> queued = new HashSet<>();
        for (var command : saved.pending()) {
            if (command == null || !saved.acceptedEvents().contains(command.eventId())
                    || !queued.add(command.eventId()) || command.on() == target) {
                throw new IllegalArgumentException("Invalid command in light queue checkpoint.");
            }
            target = command.on();
        }
        if (saved.targetOn() != target || saved.acceptedEvents().stream().anyMatch(Objects::isNull)) {
            throw new IllegalArgumentException("Invalid light queue checkpoint state.");
        }
        return new LightControl(saved, capacity);
    }

    LightControl copy() {
        return new LightControl(checkpoint(), capacity);
    }

    Checkpoint checkpoint() {
        return new Checkpoint(CHECKPOINT_VERSION, targetOn, List.copyOf(pending), Set.copyOf(acceptedEvents),
                inFlightRunId, confirmedCommandId, confirmedOn, recoveryReplayClaimed);
    }

    boolean confirmedOutputMatches(boolean observedOn) {
        return confirmedOn == observedOn;
    }

    Admission acceptPress(ButtonEventId eventId) {
        Objects.requireNonNull(eventId, "eventId");
        if (acceptedEvents.contains(eventId)) {
            return Admission.DUPLICATE;
        }
        if (pending.size() == capacity) {
            return Admission.QUEUE_FULL;
        }

        var command = new Command(eventId, !targetOn);
        pending.addLast(command);
        acceptedEvents.add(eventId);
        // Further presses build on the last accepted target while the device is catching up.
        targetOn = command.on();
        return Admission.ACCEPTED;
    }

    Optional<Command> reserveCommand(String runId) {
        if (inFlightRunId != null || pending.isEmpty()) {
            return Optional.empty();
        }
        // The command keeps its queue slot until LightPi confirms application.
        if (runId == null || runId.isBlank()) {
            throw new IllegalArgumentException("A reservation requires a LightPi run ID.");
        }
        inFlightRunId = runId;
        recoveryReplayClaimed = false;
        return Optional.of(pending.getFirst());
    }

    Optional<InFlight> inFlightCommand() {
        return inFlightRunId == null ? Optional.empty() : Optional.of(new InFlight(pending.getFirst(), inFlightRunId));
    }

    boolean claimRecoveryReplay(String lastAppliedId, boolean on) {
        if (inFlightRunId == null || recoveryReplayClaimed
                || !Objects.equals(lastAppliedId, confirmedCommandId) || on != confirmedOn) {
            return false;
        }
        recoveryReplayClaimed = true;
        return true;
    }

    boolean confirmApplied(String commandId, boolean on) {
        if (inFlightRunId == null) {
            return false;
        }
        var head = pending.getFirst();
        if (!head.commandId().equals(commandId) || head.on() != on) {
            return false;
        }
        pending.removeFirst();
        inFlightRunId = null;
        recoveryReplayClaimed = false;
        confirmedCommandId = commandId;
        confirmedOn = on;
        return true;
    }

    Snapshot snapshot() {
        return new Snapshot(targetOn, List.copyOf(pending));
    }
}
