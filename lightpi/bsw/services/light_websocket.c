#define _POSIX_C_SOURCE 200809L

#include "light_websocket.h"

#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>

enum { heartbeat_interval_ms = 30000 };

static int curl_status(CURLcode result)
{
    switch (result) {
    case CURLE_OK:
        return 0;
    case CURLE_ABORTED_BY_CALLBACK:
        return ECANCELED;
    case CURLE_OUT_OF_MEMORY:
        return ENOMEM;
    case CURLE_OPERATION_TIMEDOUT:
        return ETIMEDOUT;
    default:
        return EIO;
    }
}

static void report_curl_error(const char *operation, CURLcode result)
{
    fprintf(stderr, "light-node WebSocket %s: %s\n", operation, curl_easy_strerror(result));
}

int light_ws_open(struct light_websocket *ws, const char *url, int stop_fd)
{
    CURLcode result = websocket_transport_open(&ws->transport, url);
    if (result == CURLE_OK) {
        ws->stop_fd = stop_fd;
    } else if (result != CURLE_OUT_OF_MEMORY) {
        report_curl_error("connect", result);
    }
    return curl_status(result);
}

void light_ws_close(struct light_websocket *ws)
{
    websocket_transport_close(&ws->transport);
}

static void report_reconnect_attempt(size_t attempt, int delay_ms)
{
    printf("{\"event\":\"reconnect_attempt\",\"attempt\":%zu,\"delay_ms\":%d}\n", attempt, delay_ms);
}

static void report_reconnect_failure(size_t attempt, CURLcode result, bool exhausted)
{
    if (result != CURLE_OUT_OF_MEMORY) {
        report_curl_error("connect", result);
    }
    printf("{\"event\":\"reconnect_failed\",\"attempt\":%zu,\"error\":%d}\n", attempt, curl_status(result));
    if (exhausted) {
        puts("{\"event\":\"reconnect_exhausted\"}");
    }
}

int light_ws_reconnect(struct light_websocket *ws, const char *url)
{
    light_ws_close(ws);
    CURLcode result = websocket_transport_open_with_retry(&ws->transport, url, ws->stop_fd,
            (struct websocket_retry_observer){
                .attempt_started = report_reconnect_attempt,
                .attempt_failed = report_reconnect_failure,
            });
    return curl_status(result);
}

int light_ws_send(struct light_websocket *ws, const char *text)
{
    CURLcode result = websocket_transport_send_text(&ws->transport, text, strlen(text), ws->stop_fd);
    if (result != CURLE_OK && result != CURLE_ABORTED_BY_CALLBACK) {
        report_curl_error("send", result);
    }
    return curl_status(result);
}

static int consume_input(struct light_ws_input *input, struct light_websocket *ws, bool readable)
{
    if (input == NULL || input->fd < 0) {
        return 0;
    }
    int error = input->consume(input->context, ws, readable);
    if (error == ENODATA) {
        input->fd = -1;
        return 0;
    }
    return error;
}

int light_ws_receive(struct light_websocket *ws, char text[static LIGHT_WS_MAX_MESSAGE + 1], size_t *length,
                     struct light_ws_input *input)
{
    struct websocket_text_buffer message = {.data = text, .capacity = LIGHT_WS_MAX_MESSAGE + 1};
    for (;;) {
        int error = websocket_transport_receive_text(&ws->transport, &message);
        if (error == 0) {
            *length = message.length;
            return 0;
        }
        if (error != EAGAIN) {
            return error;
        }
        error = consume_input(input, ws, false);
        if (error != 0) {
            return error;
        }

        struct websocket_wait_events events;
        error = websocket_transport_wait(&ws->transport, POLLIN, ws->stop_fd, input == NULL ? -1 : input->fd,
                                         heartbeat_interval_ms, &events);
        if (error == ETIMEDOUT) {
            puts("{\"event\":\"heartbeat\"}");
        } else if (error != 0) {
            return error;
        } else if (events.stop != 0) {
            return ECANCELED;
        } else if (events.socket != 0 && !(events.socket & POLLIN)) {
            return ECONNRESET;
        } else if (input != NULL && events.queued != 0 && !(events.socket & POLLIN)) {
            error = consume_input(input, ws, true);
            if (error != 0) {
                return error;
            }
        }
    }
}
