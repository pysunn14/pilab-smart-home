package project.smarthome.handler;

import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class LabSessionProbe {
    @GetMapping("/pilab/sessions")
    public List<String> sessions() {
        return WebSocketHandler.labOpenPiIps();
    }

    @GetMapping("/pilab/session-identities")
    public Map<String, String> sessionIdentities() {
        return WebSocketHandler.labOpenPiSessions();
    }

    @GetMapping("/pilab/monotonic")
    public long monotonicClock() {
        return System.nanoTime();
    }
}
