package project.smarthome.handler;

import jakarta.servlet.Filter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.socket.CloseStatus;
import project.smarthome.control.ControlService;
import project.smarthome.control.LightControl;
import project.smarthome.telemetry.SensorSnapshot;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Logger;

@RestController
public class ReceiverLabSessionProbe {
    private static final Logger LOG = Logger.getLogger(ReceiverLabSessionProbe.class.getName());
    private final DeviceSessions sessions;
    private final SensorSnapshot sensors;
    private final Optional<ControlService> control;
    private final LightCommandSender lightCommands;
    private final AirServoCommands airServo;
    private final AirDustControl airDust;
    private final FireAlarmCommands fireAlarm;
    private final FireAlarmControl fireControl;
    private final BlindCommands blinds;
    private final BlindAutoControl blindAuto;
    private final String lightAddress;
    private final Map<String, String> sensorAddresses;
    private final AtomicInteger rejectedLightConnections = new AtomicInteger();

    public ReceiverLabSessionProbe(DeviceSessions sessions, SensorSnapshot sensors, Optional<ControlService> control,
                                   LightCommandSender lightCommands, AirServoCommands airServo, AirDustControl airDust,
                                   FireAlarmCommands fireAlarm, FireAlarmControl fireControl, BlindCommands blinds,
                                   BlindAutoControl blindAuto,
                                   @Value("${PILAB_LIGHT_IP:}") String lightAddress,
                                   @Value("${PILAB_AIR_IP:}") String airAddress,
                                   @Value("${PILAB_FIRE_IP:}") String fireAddress) {
        this.sessions = sessions;
        this.sensors = sensors;
        this.control = control;
        this.lightCommands = lightCommands;
        this.airServo = airServo;
        this.airDust = airDust;
        this.fireAlarm = fireAlarm;
        this.fireControl = fireControl;
        this.blinds = blinds;
        this.blindAuto = blindAuto;
        this.lightAddress = lightAddress;
        this.sensorAddresses = Map.of("airpi", airAddress, "firepi", fireAddress);
    }

    @GetMapping("/pilab/sessions")
    public List<String> sessions() {
        return List.copyOf(sessions.activeIdentities().keySet());
    }

    @GetMapping("/pilab/session-identities")
    public Map<String, String> sessionIdentities() {
        return sessions.activeIdentities();
    }

    @GetMapping("/pilab/room-state")
    public Map<String, Object> roomState(HttpServletRequest request) {
        requireLoopback(request);
        return Map.of("sessions", sessions.activeIdentities(), "readings", sensors.read());
    }

    @GetMapping("/pilab/monotonic")
    public long monotonicClock() {
        return System.nanoTime();
    }

    @GetMapping("/pilab/light-pending")
    public List<String> pendingLightCommands() {
        return control.flatMap(ControlService::lightSnapshot).orElseThrow()
                .pending().stream().map(LightControl.Command::commandId).toList();
    }

    @PostMapping("/pilab/light-state-query")
    public Map<String, Boolean> queryLightState() {
        return Map.of("requested", lightCommands.requestStateQuery());
    }

    @PostMapping("/pilab/air-servo")
    public CompletableFuture<AirServoCommands.Receipt> setAirServo(@RequestParam("angle") int angle,
                                                                  HttpServletRequest request) {
        requireLoopback(request);
        if (airDust.enabled()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "AirPi servo is in automatic mode");
        }
        return airServo.send(angle);
    }

    @PostMapping("/pilab/fire-alarm")
    public CompletableFuture<FireAlarmCommands.Receipt> setFireAlarm(@RequestParam("on") boolean on,
                                                                    HttpServletRequest request) {
        requireLoopback(request);
        if (fireControl.enabled()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "FirePi alarm is in automatic mode");
        }
        return fireAlarm.send(on);
    }

    @PostMapping("/pilab/light-blind")
    public CompletableFuture<BlindCommands.Receipt> moveBlind(
            @RequestParam("direction") BlindCommands.Direction direction, HttpServletRequest request) {
        requireLoopback(request);
        if (blindAuto.enabled()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "LightPi blind is in automatic mode");
        }
        return blinds.send(direction);
    }

    private static void requireLoopback(HttpServletRequest request) {
        if (!"127.0.0.1".equals(request.getRemoteAddr())) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN);
        }
    }

    @PostMapping("/pilab/light-disconnect")
    public Map<String, Boolean> disconnectLight(@RequestParam("sessionId") String sessionId,
            @RequestParam(name = "pendingCommandId", required = false) List<String> pendingIds) throws IOException {
        var session = sessions.currentAt(lightAddress).filter(current -> current.getId().equals(sessionId));
        var expected = pendingIds == null ? List.<String>of() : pendingIds;
        if (session.isEmpty() || !pendingLightCommands().equals(expected)) {
            return Map.of("closed", false);
        }
        session.orElseThrow().close(CloseStatus.NORMAL);
        return Map.of("closed", true);
    }

    @PostMapping("/pilab/light-connection-fault")
    public Map<String, Boolean> rejectNextLightConnections(@RequestParam("count") int count) {
        return Map.of("armed", count >= 1 && count <= 3 && rejectedLightConnections.compareAndSet(0, count));
    }

    @PostMapping("/pilab/sensor-disconnect")
    public Map<String, Boolean> disconnectSensor(@RequestParam("nodeId") String nodeId,
                                                  @RequestParam("sessionId") String sessionId) throws IOException {
        String address = sensorAddresses.get(nodeId);
        if (address == null || address.isBlank()) {
            return Map.of("closed", false);
        }
        var target = sessions.currentAt(address).filter(session -> session.getId().equals(sessionId));
        if (target.isEmpty()) {
            return Map.of("closed", false);
        }
        target.orElseThrow().close(CloseStatus.NORMAL);
        return Map.of("closed", true);
    }

    @Bean
    FilterRegistrationBean<Filter> lightConnectionFaultFilter() {
        Filter filter = (request, response, chain) -> {
            var http = (HttpServletRequest) request;
            if (lightAddress.equals(http.getRemoteAddr()) && "websocket".equalsIgnoreCase(http.getHeader("Upgrade"))) {
                int before = rejectedLightConnections.getAndUpdate(remaining -> Math.max(0, remaining - 1));
                if (before > 0) {
                    LOG.info(() -> "lab_light_handshake_rejected remaining=" + (before - 1));
                    ((HttpServletResponse) response).setStatus(HttpServletResponse.SC_SERVICE_UNAVAILABLE);
                    return;
                }
            }
            chain.doFilter(request, response);
        };
        var registration = new FilterRegistrationBean<>(filter);
        registration.addUrlPatterns("/socket");
        return registration;
    }
}
