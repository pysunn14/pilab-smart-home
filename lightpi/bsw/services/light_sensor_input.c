#include "light_sensor_input.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

static enum telemetry_sample_status encode_light(const char *line, char *values, size_t capacity)
{
    char *end;
    errno = 0;
    double value = strtod(line, &end);
    if (errno == ERANGE || end == line || *end != '\0' || !telemetry_reading_is_valid(value)) {
        fputs("light-node: invalid ambient light input\n", stderr);
        return TELEMETRY_SAMPLE_ERROR;
    }
    int length = snprintf(values, capacity, "{\"light\":%.17g}", value);
    return length > 0 && (size_t)length < capacity ? TELEMETRY_SAMPLE_READY : TELEMETRY_SAMPLE_ERROR;
}

void light_sensor_input_init(struct light_sensor_input *sensor, int fd, const char *run_id)
{
    *sensor = (struct light_sensor_input){
        .stream = {.fd = fd, .kind = "ambient light", .encode = encode_light},
        .run_id = run_id,
    };
}

int light_sensor_consume(void *context, struct light_websocket *ws, bool readable)
{
    struct light_sensor_input *sensor = context;
    for (;;) {
        char values[48];
        enum telemetry_sample_status status = telemetry_read_available(&sensor->stream, values, sizeof values, readable);
        readable = false;
        if (status == TELEMETRY_SAMPLE_WAITING) {
            return 0;
        }
        if (status == TELEMETRY_SAMPLE_END) {
            return ENODATA;
        }
        if (status == TELEMETRY_SAMPLE_ERROR) {
            return EINVAL;
        }
        char sample[256];
        if (sensor->sequence == UINT64_MAX ||
            !telemetry_format_sample(sample, sizeof sample, "lightpi", sensor->run_id, ++sensor->sequence, values)) {
            return EOVERFLOW;
        }
        int error = light_ws_send(ws, sample);
        if (error != 0) {
            printf("{\"event\":\"sensor_sample_abandoned\",\"seq\":%" PRIu64 "}\n", sensor->sequence);
            return error == ECANCELED ? error : ECONNRESET;
        }
        puts(sample);
    }
}
