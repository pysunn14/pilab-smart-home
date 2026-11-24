#ifndef LIGHTPI_LIGHT_RTE_H
#define LIGHTPI_LIGHT_RTE_H

#include "blind_swc.h"
#include "light_swc.h"

struct light_command {
    const char *command_id;
    bool on;
};

struct light_state {
    const char *last_applied_id;
    bool on;
};

struct light_rte_ports {
    struct light_swc_ports device;
    struct blind_swc_ports blind;
    int (*read_light)(void *context, bool *on);
    int (*send_applied)(void *context, const struct light_command *command);
    int (*send_state_snapshot)(void *context, const char *query_id, const struct light_state *state);
    void *send_context;
};

struct light_rte {
    struct light_rte_ports ports;
    char *last_command_id;
    bool last_on;
};

/* Initialize with connected ports and zeroed command history. This instance
 * owns the last applied ID until destroyed. All calls run on its receive task.
 * The caller supplies a validated command borrowed for this call. A matching
 * replay resends the response. Reusing the last ID with a different value
 * returns EPROTO. Device errors leave the previous applied record intact.
 */
int light_rte_receive_command(struct light_rte *rte, const struct light_command *command);
/* Dispatch on the receive task and return the output port's result to the protocol. */
int light_rte_receive_blind(const struct light_rte *rte, enum blind_direction direction);
/* Pair the current device reading with the in-process record on the receive task. */
int light_rte_on_state_query(const struct light_rte *rte, const char *query_id);
void light_rte_destroy(struct light_rte *rte);

#endif
