#include "light_rte.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

int light_rte_receive_command(struct light_rte *rte, const struct light_command *command)
{
    const struct light_rte_ports *ports = &rte->ports;
    if (rte->last_command_id != NULL && strcmp(rte->last_command_id, command->command_id) == 0) {
        if (rte->last_on != command->on) {
            return EPROTO;
        }
    } else {
        // Reserve storage first so an applied command can always be recorded.
        size_t size = strlen(command->command_id) + 1;
        char *id = malloc(size);
        if (id == NULL) {
            return ENOMEM;
        }
        memcpy(id, command->command_id, size);
        int error = light_swc_on_command(&ports->device, command->on);
        if (error != 0) {
            free(id);
            return error;
        }
        free(rte->last_command_id);
        // The write succeeded even if the next call cannot send its reply.
        rte->last_command_id = id;
        rte->last_on = command->on;
    }
    return ports->send_applied(ports->send_context, command);
}

int light_rte_receive_blind(const struct light_rte *rte, enum blind_direction direction)
{
    return blind_swc_on_command(&rte->ports.blind, direction);
}

int light_rte_on_state_query(const struct light_rte *rte, const char *query_id)
{
    const struct light_rte_ports *ports = &rte->ports;
    struct light_state state = {.last_applied_id = rte->last_command_id};
    int error = ports->read_light(ports->device.context, &state.on);
    if (error != 0) {
        return error;
    }
    return ports->send_state_snapshot(ports->send_context, query_id, &state);
}

void light_rte_destroy(struct light_rte *rte)
{
    free(rte->last_command_id);
    rte->last_command_id = NULL;
}
