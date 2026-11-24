package project.smarthome.handler;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.ObjectReader;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import project.smarthome.telemetry.SensorSnapshot;

import java.io.IOException;
import java.util.Map;

@Component
public class DeviceWebSocketHandler extends TextWebSocketHandler {
    private final ObjectMapper mapper;
    private final ObjectReader sensorReader;
    private final SensorSnapshot sensors;
    private final DeviceSessions sessions;
    private final LightCommandSender lightCommands;
    private final SensorSampleReceiver sensorSamples;
    private final AirServoCommands airServo;
    private final AirDustControl airDust;
    private final FireAlarmCommands fireAlarm;
    private final FireAlarmControl fireControl;
    private final BlindCommands blinds;

    public DeviceWebSocketHandler(ObjectMapper mapper, SensorSnapshot sensors, DeviceSessions sessions,
                                  LightCommandSender lightCommands, SensorSampleReceiver sensorSamples,
                                  AirServoCommands airServo, AirDustControl airDust, FireAlarmCommands fireAlarm,
                                  FireAlarmControl fireControl, BlindCommands blinds) {
        this.mapper = mapper;
        this.sensorReader = mapper.readerFor(new TypeReference<Map<String, Number>>() {});
        this.sensors = sensors;
        this.sessions = sessions;
        this.lightCommands = lightCommands;
        this.sensorSamples = sensorSamples;
        this.airServo = airServo;
        this.airDust = airDust;
        this.fireAlarm = fireAlarm;
        this.fireControl = fireControl;
        this.blinds = blinds;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        sessions.register(session);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        sessions.unregister(session);
        lightCommands.disconnected(session);
        airDust.disconnected(session);
        airServo.disconnected(session);
        fireControl.disconnected(session);
        fireAlarm.disconnected(session);
        blinds.disconnected(session);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws IOException {
        try {
            var payload = mapper.readTree(message.getPayload());
            if (sensorSamples.handleIfSample(payload, session)
                    || airDust.report(payload, session)
                    || fireControl.report(payload, session)
                    || airServo.handle(payload, session)
                    || fireAlarm.handle(payload, session)
                    || blinds.handle(payload, session)
                    || DeviceMessageReceiver.handle(payload, session, lightCommands)) {
                return;
            }

            Map<String, Number> readings = sensorReader.readValue(payload);
            if (readings.containsValue(null)) {
                DeviceMessageReceiver.reject(session, "Invalid JSON");
                return;
            }
            sensors.update(readings);
        } catch (JsonProcessingException error) {
            DeviceMessageReceiver.reject(session, "Invalid JSON");
        }
    }
}
