#ifndef MAINPI_BSW_BUTTON_WEBSOCKET_H
#define MAINPI_BSW_BUTTON_WEBSOCKET_H

#include "websocket_transport.h"
#include <stdint.h>

/* The network worker owns the handle for opening, receiving, sending and cleanup. */
CURLcode button_websocket_connect(struct websocket_transport *connection, const char *url, int stop_fd);
CURLcode button_websocket_wait(const struct websocket_transport *connection, int queued_fd, int stop_fd);
CURLcode button_websocket_send(const struct websocket_transport *connection, const char *run_id, uint64_t sequence);

#endif
