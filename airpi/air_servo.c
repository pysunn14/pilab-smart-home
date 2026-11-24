#include "air_servo.h"
#include "virtual_actuator.h"

static const struct virtual_actuator_protocol servo = {
    .node_id = "airpi", .command_type = "ServoCommand", .state_type = "ServoStateReported", .field = "angle",
    .applied_event = "servo_applied", .kind = VIRTUAL_ANGLE,
};

int air_servo_initialize(const char *path)
{
    return virtual_actuator_write(path, 90, true);
}

CURLcode air_servo_report(void *output_path, struct websocket_transport *transport)
{
    return virtual_actuator_report(&servo, output_path, transport);
}

CURLcode air_servo_on_text(void *output_path, struct websocket_transport *transport, const char *text, size_t length)
{
    return virtual_actuator_handle(&servo, output_path, transport, text, length);
}
