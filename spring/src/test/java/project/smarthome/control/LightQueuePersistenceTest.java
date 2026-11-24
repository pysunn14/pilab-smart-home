package project.smarthome.control;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class LightQueuePersistenceTest {
    @TempDir Path directory;
    private final ObjectMapper mapper = new ObjectMapper();
    private final ButtonEventId first = new ButtonEventId("mainpi", "main-run", -2);
    private final ButtonEventId second = new ButtonEventId("mainpi", "main-run", -1);

    @Test
    void restoresQueueAndReservationAndKeepsDeduplicationAfterConfirmation() throws IOException {
        var file = directory.resolve("light-queue.json");
        var before = new ControlService(2, file, mapper);
        assertTrue(before.initializeLightState(false));
        assertEquals(LightControl.Admission.ACCEPTED, before.acceptButtonPress(first));
        var on = before.reserveLightCommand("light-run").orElseThrow();
        assertEquals(LightControl.Admission.ACCEPTED, before.acceptButtonPress(second));

        var recovering = new ControlService(2, file, mapper);
        assertFalse(recovering.initializeLightState(true));
        assertEquals(before.lightSnapshot(), recovering.lightSnapshot());
        assertEquals(new LightControl.InFlight(on, "light-run"),
                recovering.inFlightLightCommand().orElseThrow());
        assertTrue(recovering.confirmLightApplied(on.commandId(), true));

        // Restart again between confirming ON and reserving the queued OFF.
        var ready = new ControlService(2, file, mapper);
        assertTrue(ready.confirmedLightOutputMatches(true));
        assertTrue(ready.inFlightLightCommand().isEmpty());
        assertEquals(LightControl.Admission.DUPLICATE, ready.acceptButtonPress(first));
        var off = ready.reserveLightCommand("light-run").orElseThrow();
        assertEquals(new LightControl.Command(second, false), off);
        assertTrue(ready.confirmLightApplied(off.commandId(), false));

        var drained = new ControlService(2, file, mapper);
        assertTrue(drained.lightSnapshot().orElseThrow().pending().isEmpty());
        assertTrue(drained.confirmedLightOutputMatches(false));
        assertEquals(LightControl.Admission.DUPLICATE, drained.acceptButtonPress(second));
    }

    @Test
    void restoresTheSpentReplayBudget() throws IOException {
        var file = directory.resolve("light-queue.json");
        var before = new ControlService(2, file, mapper);
        before.initializeLightState(false);
        before.acceptButtonPress(first);
        before.reserveLightCommand("light-run").orElseThrow();
        assertTrue(before.claimLightRecoveryReplay(null, false));

        var after = new ControlService(2, file, mapper);
        assertFalse(after.claimLightRecoveryReplay(null, false));
        assertEquals("light-run", after.inFlightLightCommand().orElseThrow().runId());
    }

    @Test
    void stopsQueueMutationsAfterAWriteFailure() throws IOException {
        var file = directory.resolve("light-queue.json");
        var control = new ControlService(2, file, mapper);
        control.initializeLightState(false);
        var before = control.lightSnapshot();
        // Make replacing the target file fail while temporary files can still be created.
        Files.delete(file);
        Files.createDirectory(file);
        assertThrows(UncheckedIOException.class, () -> control.acceptButtonPress(first));
        assertEquals(before, control.lightSnapshot());
        assertThrows(IllegalStateException.class, () -> control.reserveLightCommand("light-run"));
        try (var entries = Files.list(directory)) {
            assertEquals(1, entries.count());
        }
    }

    @Test
    void refusesToStartWithAnInvalidCheckpoint() throws IOException {
        var file = directory.resolve("light-queue.json");
        Files.writeString(file, "{}");
        assertThrows(IOException.class, () -> new ControlService(2, file, mapper));
    }
}
