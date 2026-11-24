package project.smarthome.control;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ControlServiceTest {
    private final ButtonEventId first = new ButtonEventId("mainpi", "run-a", 1);
    private final ButtonEventId second = new ButtonEventId("mainpi", "run-a", 2);

    @Test
    void queuesOnThenOffForTwoDistinctPresses() {
        var control = readyControl(false, 2);

        assertEquals(LightControl.Admission.ACCEPTED, control.acceptButtonPress(first));
        assertEquals(LightControl.Admission.ACCEPTED, control.acceptButtonPress(second));

        assertEquals(new LightControl.Snapshot(false, List.of(
                new LightControl.Command(first, true), new LightControl.Command(second, false))),
                control.lightSnapshot().orElseThrow());
    }

    @Test
    void concurrentPressesPreserveEveryCommandAndConsistentSnapshots() throws Exception {
        int workers = 4;
        int pressesPerWorker = 128;
        int totalPresses = workers * pressesPerWorker;
        var control = readyControl(false, totalPresses);
        var start = new CyclicBarrier(workers);
        var executor = Executors.newFixedThreadPool(workers);
        var submissions = new ArrayList<Future<?>>();
        try {
            for (int worker = 0; worker < workers; worker++) {
                int firstSequence = worker * pressesPerWorker + 1;
                submissions.add(executor.submit(() -> {
                    start.await(5, TimeUnit.SECONDS);
                    for (int sequence = firstSequence; sequence < firstSequence + pressesPerWorker; sequence++) {
                        var eventId = new ButtonEventId("mainpi", "run-a", sequence);
                        assertEquals(LightControl.Admission.ACCEPTED, control.acceptButtonPress(eventId));
                        var snapshot = control.lightSnapshot().orElseThrow();
                        assertEquals(snapshot.pending().size() % 2 == 1, snapshot.targetOn());
                    }
                    return null;
                }));
            }
            for (var submission : submissions) {
                submission.get(5, TimeUnit.SECONDS);
            }
        } finally {
            executor.shutdownNow();
        }

        var commands = control.lightSnapshot().orElseThrow().pending();
        assertEquals(totalPresses, commands.size());
        assertEquals(totalPresses, commands.stream().map(LightControl.Command::eventId).distinct().count());
        for (int i = 0; i < commands.size(); i++) {
            assertEquals(i % 2 == 0, commands.get(i).on(), "command " + i);
        }
    }

    @Test
    void duplicatePressIsRecognizedEvenWhenTheQueueIsFull() {
        var control = readyControl(false, 1);

        assertEquals(LightControl.Admission.ACCEPTED, control.acceptButtonPress(first));
        assertEquals(LightControl.Admission.DUPLICATE, control.acceptButtonPress(first));

        assertEquals(new LightControl.Snapshot(true, List.of(new LightControl.Command(first, true))),
                control.lightSnapshot().orElseThrow());
    }

    @Test
    void fullQueueRejectsNewPressAndPreservesTheAcceptedTarget() {
        var control = readyControl(false, 1);
        control.acceptButtonPress(first);
        var before = control.lightSnapshot();

        assertEquals(LightControl.Admission.QUEUE_FULL, control.acceptButtonPress(second));

        assertEquals(before, control.lightSnapshot());
    }

    @Test
    void sameSequenceInANewRunIsADistinctPress() {
        var control = readyControl(true, 2);
        var restarted = new ButtonEventId("mainpi", "run-b", 1);

        assertEquals(LightControl.Admission.ACCEPTED, control.acceptButtonPress(first));
        assertEquals(LightControl.Admission.ACCEPTED, control.acceptButtonPress(restarted));

        assertEquals(new LightControl.Snapshot(true, List.of(
                new LightControl.Command(first, false), new LightControl.Command(restarted, true))),
                control.lightSnapshot().orElseThrow());
    }

    @Test
    void recoveryReplayNeedsTheConfirmedPredecessorAndCanBeClaimedOnce() {
        var control = readyControl(false, 2);
        control.acceptButtonPress(first);
        control.reserveLightCommand("light-run");
        var previous = new LightControl.Command(first, true);
        assertTrue(control.confirmLightApplied(previous.commandId(), true));
        control.acceptButtonPress(second);
        assertFalse(control.claimLightRecoveryReplay(previous.commandId(), true));
        var head = control.reserveLightCommand("light-run").orElseThrow();

        assertFalse(control.claimLightRecoveryReplay(null, true));
        assertFalse(control.claimLightRecoveryReplay(previous.commandId(), false));
        assertTrue(control.claimLightRecoveryReplay(previous.commandId(), true));
        assertFalse(control.claimLightRecoveryReplay(previous.commandId(), true));
        assertEquals(new LightControl.InFlight(head, "light-run"), control.inFlightLightCommand().orElseThrow());
    }

    private static ControlService readyControl(boolean initialOn, int capacity) {
        var control = new ControlService(capacity);
        control.initializeLightState(initialOn);
        return control;
    }
}
