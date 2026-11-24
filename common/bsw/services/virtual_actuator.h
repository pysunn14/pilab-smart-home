#ifndef PILAB_BSW_VIRTUAL_ACTUATOR_H
#define PILAB_BSW_VIRTUAL_ACTUATOR_H

#include "websocket_transport.h"

enum virtual_actuator_value { VIRTUAL_SWITCH, VIRTUAL_ANGLE };

struct virtual_actuator_protocol {
    const char *node_id;
    const char *command_type;
    const char *state_type;
    const char *field;
    const char *applied_event;
    enum virtual_actuator_value kind;
};

/* Atomically publish the read-back value. Initial creation requires an unused path. */
int virtual_actuator_write(const char *path, int value, bool initialize);
CURLcode virtual_actuator_report(const struct virtual_actuator_protocol *protocol, const char *path,
                                 struct websocket_transport *transport);
CURLcode virtual_actuator_handle(const struct virtual_actuator_protocol *protocol, const char *path,
                                 struct websocket_transport *transport, const char *text, size_t length);

#endif
