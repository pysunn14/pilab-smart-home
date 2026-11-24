package project.smarthome.telemetry;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
public class HomeDataController {
    private final SensorSnapshot sensors;

    public HomeDataController(SensorSnapshot sensors) {
        this.sensors = sensors;
    }

    @GetMapping("/home/data")
    public Map<String, Double> data() {
        return sensors.read();
    }
}
