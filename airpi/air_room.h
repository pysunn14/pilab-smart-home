#ifndef PILAB_AIR_ROOM_H
#define PILAB_AIR_ROOM_H

#include "telemetry_publisher.h"

struct air_room;

/* The sampler owns its thread and descriptors until air_room_stop(). */
struct air_room *air_room_start(const char *path, telemetry_encode_sample validate);
int air_room_ready_fd(const struct air_room *room);
enum telemetry_sample_status air_room_take(void *context, char *values, size_t capacity);
void air_room_stop(struct air_room *room);

#endif
