package project.smarthome.control;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.Optional;

import static java.nio.file.StandardCopyOption.ATOMIC_MOVE;
import static java.nio.file.StandardCopyOption.REPLACE_EXISTING;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

/** Single-writer checkpoint on a local filesystem that supports atomic replacement. */
final class LightQueueFile {
    private final Path file;
    private final ObjectMapper mapper;

    LightQueueFile(Path file, ObjectMapper mapper) {
        this.file = file.toAbsolutePath();
        this.mapper = mapper;
    }

    Optional<LightControl> load(int capacity) throws IOException {
        try (var input = Files.newInputStream(file)) {
            var saved = mapper.readValue(input, LightControl.Checkpoint.class);
            return Optional.of(LightControl.restore(saved, capacity));
        } catch (NoSuchFileException absent) {
            return Optional.empty();
        } catch (IllegalArgumentException invalid) {
            throw new IOException("Cannot restore the light queue checkpoint.", invalid);
        }
    }

    void save(LightControl.Checkpoint checkpoint) throws IOException {
        var parent = file.getParent();
        Files.createDirectories(parent);
        var temporary = Files.createTempFile(parent, ".light-queue-", ".tmp");
        try {
            var bytes = ByteBuffer.wrap(mapper.writeValueAsBytes(checkpoint));
            try (var output = FileChannel.open(temporary, WRITE)) {
                while (bytes.hasRemaining()) {
                    output.write(bytes);
                }
                output.force(true);
            }
            Files.move(temporary, file, ATOMIC_MOVE, REPLACE_EXISTING);
            try (var directory = FileChannel.open(parent, READ)) {
                directory.force(true);
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }
}
