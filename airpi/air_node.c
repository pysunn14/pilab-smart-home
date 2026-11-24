#include "air_room.h"
#include "air_servo.h"

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int fail_websocket(const char *stage, CURLcode result)
{
    fprintf(stderr, "air-node WebSocket %s: %s\n", stage, curl_easy_strerror(result));
    return EXIT_FAILURE;
}

static bool read_number(const char **text, double *value)
{
    char *end;
    errno = 0;
    *value = strtod(*text, &end);
    if (errno == ERANGE || end == *text) {
        return false;
    }
    *text = end;
    return true;
}

enum air_input { dust_only, dust_or_climate, room_snapshot };

static enum telemetry_sample_status encode_air_readings(const char *text, char *values, size_t capacity,
                                                        enum air_input input)
{
    double dust;
    if (!read_number(&text, &dust) || !telemetry_reading_is_valid(dust) || (input == dust_only && *text != '\0')) {
        fputs("air-node: dust value must be non-negative and fit the sensor numeric range\n", stderr);
        return TELEMETRY_SAMPLE_ERROR;
    }

    int length;
    if (*text == '\0') {
        if (input == room_snapshot) {
            fputs("air-node: a room sample requires dust, temperature and humidity\n", stderr);
            return TELEMETRY_SAMPLE_ERROR;
        }
        length = snprintf(values, capacity, "{\"dust\":%.17g}", dust);
    } else {
        double temperature, humidity;
        if (!isspace((unsigned char)*text) || !read_number(&text, &temperature)
                || !telemetry_reading_is_valid(fabs(temperature))
                || !isspace((unsigned char)*text) || !read_number(&text, &humidity)
                || !(humidity >= 0.0 && humidity <= 100.0) || *text != '\0') {
            fputs("air-node: expected dust, temperature in Celsius, and humidity from 0 to 100 percent\n", stderr);
            return TELEMETRY_SAMPLE_ERROR;
        }
        length = snprintf(values, capacity, "{\"dust\":%.17g,\"temperature\":%.17g,\"humidity\":%.17g}",
                          dust, temperature, humidity);
    }
    if (length < 0 || (size_t)length >= capacity) {
        fputs("air-node: cannot encode air quality sample\n", stderr);
        return TELEMETRY_SAMPLE_ERROR;
    }
    return TELEMETRY_SAMPLE_READY;
}

static enum telemetry_sample_status encode_stream_sample(const char *text, char *values, size_t capacity)
{
    return encode_air_readings(text, values, capacity, dust_or_climate);
}

static enum telemetry_sample_status encode_room_sample(const char *text, char *values, size_t capacity)
{
    return encode_air_readings(text, values, capacity, room_snapshot);
}

int main(int argc, char **argv)
{
    const char *usage = "usage: air-node WS_URL {DUST_VALUE|--stdin|--room-file PATH [--servo-file PATH]}\n";
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        fputs(usage, stdout);
        return EXIT_SUCCESS;
    }
    bool servo_enabled = argc == 6 && strcmp(argv[4], "--servo-file") == 0 && argv[5][0] != '\0';
    bool from_room = (argc == 4 || servo_enabled) && strcmp(argv[2], "--room-file") == 0 && argv[3][0] != '\0';
    if (!from_room && (argc != 3 || strcmp(argv[2], "--room-file") == 0)) {
        fputs(usage, stderr);
        return EXIT_FAILURE;
    }

    bool from_stdin = strcmp(argv[2], "--stdin") == 0;
    struct telemetry_stream stream = {.fd = STDIN_FILENO, .kind = "dust", .encode = encode_stream_sample};
    char values[128];
    if (!from_room) {
        enum telemetry_sample_status sample = from_stdin ? telemetry_read_first_sample(&stream, values, sizeof values)
            : encode_air_readings(argv[2], values, sizeof values, dust_only);
        if (sample != TELEMETRY_SAMPLE_READY) {
            return sample == TELEMETRY_SAMPLE_END ? EXIT_SUCCESS : EXIT_FAILURE;
        }
    }

    if (servo_enabled) {
        int error = air_servo_initialize(argv[5]);
        if (error != 0) {
            fprintf(stderr, "air-node: initialize virtual servo: %s\n", strerror(error));
            return EXIT_FAILURE;
        }
    }

    CURLcode result = curl_global_init(CURL_GLOBAL_DEFAULT);
    if (result != CURLE_OK) {
        return fail_websocket("initialization", result);
    }

    struct air_room *room = NULL;
    if (from_room) {
        room = air_room_start(argv[3], encode_room_sample);
        if (room == NULL) {
            perror("air-node: start room sampler");
            curl_global_cleanup();
            return EXIT_FAILURE;
        }
        stream = (struct telemetry_stream){
            .fd = air_room_ready_fd(room), .take_ready = air_room_take, .context = room,
        };
        if (servo_enabled) {
            stream.on_connected = air_servo_report;
            stream.on_text = air_servo_on_text;
            stream.receiver_context = argv[5];
        }
    }

    struct websocket_transport transport = {0};
    result = websocket_transport_open_with_retry(&transport, argv[1], -1, (struct websocket_retry_observer){0});
    int status = result == CURLE_OK
        ? telemetry_publish_samples(&transport, argv[1], "airpi", values, sizeof values,
                                    from_stdin || from_room ? &stream : NULL)
        : fail_websocket("connect", result);
    websocket_transport_close(&transport);
    if (room != NULL) {
        air_room_stop(room);
    }
    curl_global_cleanup();
    return status;
}
