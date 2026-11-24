#include "telemetry_publisher.h"
#include "virtual_actuator.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static bool parse_sample(const char *line, double *infra, double *gas)
{
    char *end;
    errno = 0;
    *infra = strtod(line, &end);
    if (errno == ERANGE || end == line || !isspace((unsigned char)*end) || !telemetry_reading_is_valid(*infra)) {
        return false;
    }
    const char *gas_start = end;
    *gas = strtod(gas_start, &end);
    return errno != ERANGE && end != gas_start && *end == '\0' && telemetry_reading_is_valid(*gas);
}

static enum telemetry_sample_status encode_sample(const char *line, char *values, size_t capacity)
{
    double infra, gas;
    if (!parse_sample(line, &infra, &gas)) {
        fputs("fire-node: expected two non-negative values in the sensor numeric range for infra and gas\n", stderr);
        return TELEMETRY_SAMPLE_ERROR;
    }
    int length = snprintf(values, capacity, "{\"infra\":%.17g,\"gas\":%.17g}", infra, gas);
    if (length < 0 || (size_t)length >= capacity) {
        fputs("fire-node: cannot encode infrared and gas sample\n", stderr);
        return TELEMETRY_SAMPLE_ERROR;
    }
    return TELEMETRY_SAMPLE_READY;
}

static const struct virtual_actuator_protocol alarm_protocol = {
    .node_id = "firepi", .command_type = "FireAlarmCommand", .state_type = "FireAlarmStateReported", .field = "on",
    .applied_event = "fire_alarm_applied", .kind = VIRTUAL_SWITCH,
};

static CURLcode alarm_report(void *output_path, struct websocket_transport *transport)
{
    return virtual_actuator_report(&alarm_protocol, output_path, transport);
}

static CURLcode alarm_on_text(void *output_path, struct websocket_transport *transport, const char *text, size_t length)
{
    return virtual_actuator_handle(&alarm_protocol, output_path, transport, text, length);
}

int main(int argc, char **argv)
{
    const char *usage = "usage: fire-node WS_URL --stdin [--alarm-file PATH]\n";
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        fputs(usage, stdout);
        return EXIT_SUCCESS;
    }
    bool alarm_enabled = argc == 5 && strcmp(argv[3], "--alarm-file") == 0 && argv[4][0] != '\0';
    if ((argc != 3 && !alarm_enabled) || strcmp(argv[2], "--stdin") != 0) {
        fputs(usage, stderr);
        return EXIT_FAILURE;
    }

    struct telemetry_stream stream = {.fd = STDIN_FILENO, .kind = "infrared and gas", .encode = encode_sample};
    char values[96];
    enum telemetry_sample_status sample = telemetry_read_first_sample(&stream, values, sizeof values);
    if (sample != TELEMETRY_SAMPLE_READY) {
        return sample == TELEMETRY_SAMPLE_END ? EXIT_SUCCESS : EXIT_FAILURE;
    }
    if (alarm_enabled) {
        int error = virtual_actuator_write(argv[4], 0, true);
        if (error != 0) {
            fprintf(stderr, "fire-node: initialize virtual alarm: %s\n", strerror(error));
            return EXIT_FAILURE;
        }
        stream.on_text = alarm_on_text;
        stream.on_connected = alarm_report;
        stream.receiver_context = argv[4];
    }

    CURLcode result = curl_global_init(CURL_GLOBAL_DEFAULT);
    if (result != CURLE_OK) {
        fprintf(stderr, "fire-node WebSocket initialization: %s\n", curl_easy_strerror(result));
        return EXIT_FAILURE;
    }

    struct websocket_transport transport = {0};
    result = websocket_transport_open_with_retry(&transport, argv[1], -1, (struct websocket_retry_observer){0});
    int status = EXIT_FAILURE;
    if (result == CURLE_OK) {
        status = telemetry_publish_samples(&transport, argv[1], "firepi", values, sizeof values, &stream);
    } else {
        fprintf(stderr, "fire-node WebSocket connect: %s\n", curl_easy_strerror(result));
    }
    websocket_transport_close(&transport);
    curl_global_cleanup();
    return status;
}
