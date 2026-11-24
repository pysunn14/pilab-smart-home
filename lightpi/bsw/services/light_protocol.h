#ifndef LIGHTPI_LIGHT_PROTOCOL_H
#define LIGHTPI_LIGHT_PROTOCOL_H

#include "light_rte.h"
#include "light_websocket.h"

int light_protocol_send_state(struct light_websocket *ws, const char *run_id, bool on);
int light_protocol_send_applied(void *context, const struct light_command *command);
int light_protocol_send_snapshot(void *context, const char *query_id, const struct light_state *state);
int light_protocol_handle(struct light_rte *rte, const char *text, size_t length);

#endif
