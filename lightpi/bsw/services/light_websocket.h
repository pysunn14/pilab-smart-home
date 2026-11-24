#ifndef LIGHTPI_LIGHT_WEBSOCKET_H
#define LIGHTPI_LIGHT_WEBSOCKET_H

#include "websocket_transport.h"

enum { LIGHT_WS_MAX_MESSAGE = 4096 };

struct light_websocket {
    struct websocket_transport transport;
    int stop_fd;
};

struct light_ws_input {
    int fd;
    void *context;
    int (*consume)(void *context, struct light_websocket *ws, bool readable);
};

int light_ws_open(struct light_websocket *ws, const char *url, int stop_fd);
void light_ws_close(struct light_websocket *ws);
int light_ws_reconnect(struct light_websocket *ws, const char *url);
int light_ws_send(struct light_websocket *ws, const char *text);
/* A source returning ENODATA is removed while the WebSocket stays open. */
int light_ws_receive(struct light_websocket *ws, char text[static LIGHT_WS_MAX_MESSAGE + 1], size_t *length,
                     struct light_ws_input *input);

#endif
