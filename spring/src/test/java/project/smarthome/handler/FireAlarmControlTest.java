package project.smarthome.handler;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FireAlarmControlTest {
    @Test
    void appliesTheOriginalInfraredAndGasBoundariesIndependently() {
        assertFalse(FireAlarmControl.alarmFor(801, 399));
        assertTrue(FireAlarmControl.alarmFor(800, 399));
        assertTrue(FireAlarmControl.alarmFor(801, 400));
        assertTrue(FireAlarmControl.alarmFor(800, 400));
    }
}
