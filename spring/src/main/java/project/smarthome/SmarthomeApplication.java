package project.smarthome;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import project.smarthome.control.ControlService;

import java.io.IOException;
import java.nio.file.Path;

@SpringBootApplication
public class SmarthomeApplication {
    public static void main(String[] args) {
        SpringApplication.run(SmarthomeApplication.class, args);
    }

    @Bean
    @ConditionalOnProperty(name = "pilab.control.light.queue-capacity")
    ControlService controlService(@Value("${pilab.control.light.queue-capacity}") int capacity,
                                  @Value("${pilab.control.light.state-file:}") String stateFile,
                                  ObjectMapper mapper) throws IOException {
        return stateFile.isBlank() ? new ControlService(capacity)
                : new ControlService(capacity, Path.of(stateFile), mapper);
    }
}
