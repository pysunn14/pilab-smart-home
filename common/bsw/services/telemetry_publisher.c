#include "telemetry_publisher.h"

#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <unistd.h>

enum { RUN_ID_BYTES = 16, RUN_ID_LENGTH = 2 * RUN_ID_BYTES };

static bool make_run_id(char run_id[static RUN_ID_LENGTH + 1])
{
    unsigned char bytes[RUN_ID_BYTES];
    ssize_t received;
    do {
        received = getrandom(bytes, sizeof bytes, 0);
    } while (received < 0 && errno == EINTR);
    if (received < 0) {
        perror("telemetry: generate run ID");
        return false;
    }
    if ((size_t)received != sizeof bytes) {
        fputs("telemetry: incomplete run ID randomness\n", stderr);
        return false;
    }

    const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof bytes; i++) {
        run_id[2 * i] = hex[bytes[i] >> 4];
        run_id[2 * i + 1] = hex[bytes[i] & 0xf];
    }
    run_id[RUN_ID_LENGTH] = '\0';
    return true;
}

static bool buffered_line_ready(const struct telemetry_stream *stream)
{
    return stream->eof || stream->buffered == sizeof stream->buffer
        || memchr(stream->buffer, '\n', stream->buffered) != NULL
        || memchr(stream->buffer, '\0', stream->buffered) != NULL;
}

static bool read_input(struct telemetry_stream *stream)
{
    ssize_t count;
    do {
        count = read(stream->fd, stream->buffer + stream->buffered, sizeof stream->buffer - stream->buffered);
    } while (count < 0 && errno == EINTR);
    if (count < 0) {
        perror("telemetry: read input");
        return false;
    }
    stream->buffered += (size_t)count;
    stream->eof = count == 0;
    return true;
}

static enum telemetry_sample_status take_sample(struct telemetry_stream *stream, char *values, size_t capacity)
{
    if (stream->eof && stream->buffered == 0) {
        return TELEMETRY_SAMPLE_END;
    }
    const char *newline = memchr(stream->buffer, '\n', stream->buffered);
    size_t length = newline == NULL ? stream->buffered : (size_t)(newline - stream->buffer);
    if (length == sizeof stream->buffer || memchr(stream->buffer, '\0', length) != NULL) {
        fprintf(stderr, "telemetry: invalid %s input line\n", stream->kind);
        return TELEMETRY_SAMPLE_ERROR;
    }

    size_t consumed = length + (newline != NULL);
    if (length > 0 && stream->buffer[length - 1] == '\r') {
        --length;
    }
    char line[sizeof stream->buffer];
    memcpy(line, stream->buffer, length);
    line[length] = '\0';
    stream->buffered -= consumed;
    memmove(stream->buffer, stream->buffer + consumed, stream->buffered);
    return stream->encode(line, values, capacity);
}

enum telemetry_sample_status telemetry_read_first_sample(struct telemetry_stream *stream,
                                                         char *values, size_t capacity)
{
    while (!buffered_line_ready(stream)) {
        if (!read_input(stream)) {
            return TELEMETRY_SAMPLE_ERROR;
        }
    }
    return take_sample(stream, values, capacity);
}

bool telemetry_reading_is_valid(double value)
{
    // Spring rounds the dashboard value via a signed 64-bit integer in hundredths.
    return value >= 0.0 && value < (double)INT64_MAX / 100.0;
}

enum telemetry_sample_status telemetry_read_available(struct telemetry_stream *stream,
                                                       char *values, size_t capacity, bool readable)
{
    if (!buffered_line_ready(stream) && readable && !read_input(stream)) {
        return TELEMETRY_SAMPLE_ERROR;
    }
    return buffered_line_ready(stream)
        ? take_sample(stream, values, capacity) : TELEMETRY_SAMPLE_WAITING;
}

bool telemetry_format_sample(char *message, size_t capacity, const char *node_id, const char *run_id,
                             uint64_t sequence, const char *values)
{
    int length = snprintf(message, capacity,
                          "{\"type\":\"SensorSample\",\"nodeId\":\"%s\",\"runId\":\"%s\","
                          "\"payload\":{\"seq\":%" PRIu64 ",\"values\":%s}}",
                          node_id, run_id, sequence, values);
    return length > 0 && (size_t)length < capacity;
}

static void report_reconnect_attempt(size_t attempt, int delay_ms)
{
    printf("{\"event\":\"reconnect_attempt\",\"attempt\":%zu,\"delay_ms\":%d}\n", attempt, delay_ms);
    fflush(stdout);
}

static void report_reconnect_failure(size_t attempt, CURLcode result, bool exhausted)
{
    printf("{\"event\":\"reconnect_failed\",\"attempt\":%zu,\"error\":%d}\n", attempt, (int)result);
    if (exhausted) {
        puts("{\"event\":\"reconnect_exhausted\"}");
    }
    fflush(stdout);
}

static bool report_connection(struct websocket_transport *transport, struct telemetry_stream *stream)
{
    if (stream == NULL || stream->on_connected == NULL) {
        return true;
    }
    CURLcode result = stream->on_connected(stream->receiver_context, transport);
    if (result != CURLE_OK) {
        fprintf(stderr, "telemetry WebSocket state report: %s\n", curl_easy_strerror(result));
    }
    return result == CURLE_OK;
}

static bool reconnect(struct websocket_transport *transport, const char *url, struct telemetry_stream *stream)
{
    websocket_transport_close(transport);
    CURLcode result = websocket_transport_open_with_retry(transport, url, -1, (struct websocket_retry_observer){
        .attempt_started = report_reconnect_attempt,
        .attempt_failed = report_reconnect_failure,
    });
    if (result != CURLE_OK) {
        fprintf(stderr, "telemetry WebSocket reconnect: %s\n", curl_easy_strerror(result));
        return false;
    }
    if (!report_connection(transport, stream)) {
        return false;
    }
    puts("{\"event\":\"reconnected\"}");
    fflush(stdout);
    return true;
}

static CURLcode service_socket(struct websocket_transport *transport, struct telemetry_stream *stream,
                               struct websocket_text_buffer *incoming)
{
    if (stream == NULL || stream->on_text == NULL) {
        return websocket_transport_discard_pending(transport);
    }
    for (;;) {
        int error = websocket_transport_receive_text(transport, incoming);
        if (error == EAGAIN) {
            return CURLE_OK;
        }
        if (error != 0) {
            fprintf(stderr, "telemetry WebSocket command receive: %s\n", strerror(error));
            return CURLE_RECV_ERROR;
        }
        CURLcode result = stream->on_text(stream->receiver_context, transport, incoming->data, incoming->length);
        incoming->length = 0;
        if (result != CURLE_OK) {
            return result;
        }
    }
}

static bool check_waiting_connection(struct websocket_transport *transport, const char *url, short socket_events,
                                     struct telemetry_stream *stream, struct websocket_text_buffer *incoming)
{
    CURLcode result = service_socket(transport, stream, incoming);
    if (result == CURLE_OK && (socket_events & (POLLERR | POLLHUP | POLLNVAL)) == 0) {
        return true;
    }
    fprintf(stderr, "telemetry WebSocket while waiting: %s\n",
            result == CURLE_OK ? "socket closed or failed" : curl_easy_strerror(result));
    puts("{\"event\":\"connection_lost\"}");
    incoming->length = 0;
    return reconnect(transport, url, stream);
}

static enum telemetry_sample_status wait_for_sample(struct websocket_transport *transport, const char *url,
                                                    struct telemetry_stream *stream, char *values, size_t capacity,
                                                    struct websocket_text_buffer *incoming)
{
    short socket_events = 0;
    bool sample_ready = false;
    for (;;) {
        if (stream->eof && stream->buffered == 0) {
            return TELEMETRY_SAMPLE_END;
        }
        // Resolve the socket before assigning an ID to any complete buffered line.
        if (!check_waiting_connection(transport, url, socket_events, stream, incoming)) {
            return TELEMETRY_SAMPLE_ERROR;
        }
        if (sample_ready) {
            return stream->take_ready(stream->context, values, capacity);
        }
        if (buffered_line_ready(stream)) {
            return take_sample(stream, values, capacity);
        }

        struct websocket_wait_events events;
        int error = websocket_transport_wait(transport, POLLIN, -1, stream->fd, -1, &events);
        if (error != 0) {
            fprintf(stderr, "telemetry wait for input or WebSocket: %s\n", strerror(error));
            return TELEMETRY_SAMPLE_ERROR;
        }
        socket_events = events.socket;
        // Leave a coalescing source pending across reconnect so it can keep replacing its samples.
        sample_ready = events.queued != 0 && stream->take_ready != NULL;
        if (events.queued != 0 && !sample_ready && !read_input(stream)) {
            return TELEMETRY_SAMPLE_ERROR;
        }
    }
}

int telemetry_publish_samples(struct websocket_transport *transport, const char *url, const char *node_id,
                              char *values, size_t capacity, struct telemetry_stream *stream)
{
    char run_id[RUN_ID_LENGTH + 1];
    if (!make_run_id(run_id)) {
        return EXIT_FAILURE;
    }
    uint64_t sequence = 1;
    if (!report_connection(transport, stream) && !reconnect(transport, url, stream)) {
        return EXIT_FAILURE;
    }
    char message[256];
    char command[512];
    struct websocket_text_buffer incoming = {.data = command, .capacity = sizeof command};
    if (stream != NULL && stream->take_ready != NULL) {
        enum telemetry_sample_status status = wait_for_sample(transport, url, stream, values, capacity, &incoming);
        if (status != TELEMETRY_SAMPLE_READY) {
            return status == TELEMETRY_SAMPLE_END ? EXIT_SUCCESS : EXIT_FAILURE;
        }
    }

    for (;;) {
        if (!telemetry_format_sample(message, sizeof message, node_id, run_id, sequence, values)) {
            fputs("telemetry: cannot encode SensorSample\n", stderr);
            return EXIT_FAILURE;
        }
        // A WebSocket close can already be pending when the next sample arrives.
        const char *operation = "check connection";
        CURLcode result = service_socket(transport, stream, &incoming);
        if (result == CURLE_OK) {
            operation = "send";
            result = websocket_transport_send_text(transport, message, strlen(message), -1);
        }
        if (result != CURLE_OK) {
            fprintf(stderr, "telemetry WebSocket %s for seq %" PRIu64 ": %s\n",
                    operation, sequence, curl_easy_strerror(result));
            if (stream == NULL) {
                return EXIT_FAILURE;
            }
            printf("{\"event\":\"sensor_sample_abandoned\",\"seq\":%" PRIu64 "}\n", sequence);
            fflush(stdout);
            incoming.length = 0;
            if (!reconnect(transport, url, stream)) {
                return EXIT_FAILURE;
            }
        } else {
            puts(message);
            fflush(stdout);
        }

        if (stream == NULL) {
            return EXIT_SUCCESS;
        }
        enum telemetry_sample_status status = wait_for_sample(transport, url, stream, values, capacity, &incoming);
        if (status != TELEMETRY_SAMPLE_READY) {
            return status == TELEMETRY_SAMPLE_END ? EXIT_SUCCESS : EXIT_FAILURE;
        }
        if (sequence == UINT64_MAX) {
            fputs("telemetry: sample sequence exhausted\n", stderr);
            return EXIT_FAILURE;
        }
        sequence++;
    }
}
