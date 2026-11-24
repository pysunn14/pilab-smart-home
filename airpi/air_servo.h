#ifndef PILAB_AIR_SERVO_H
#define PILAB_AIR_SERVO_H

#include "websocket_transport.h"

/* Create the virtual output at its initial 90 degrees. path must be unused. */
int air_servo_initialize(const char *path);
CURLcode air_servo_report(void *output_path, struct websocket_transport *transport);
CURLcode air_servo_on_text(void *output_path, struct websocket_transport *transport, const char *text, size_t length);

#endif
