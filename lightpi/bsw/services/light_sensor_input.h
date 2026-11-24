#ifndef LIGHTPI_LIGHT_SENSOR_INPUT_H
#define LIGHTPI_LIGHT_SENSOR_INPUT_H

#include "light_websocket.h"
#include "telemetry_publisher.h"

struct light_sensor_input {
    struct telemetry_stream stream;
    const char *run_id;
    uint64_t sequence;
};

void light_sensor_input_init(struct light_sensor_input *sensor, int fd, const char *run_id);
int light_sensor_consume(void *context, struct light_websocket *ws, bool readable);

#endif
