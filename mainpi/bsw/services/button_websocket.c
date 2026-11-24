#define _POSIX_C_SOURCE 200809L

#include "button_websocket.h"

#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <stdio.h>
#include <sys/eventfd.h>

static void report_connect_failure(size_t attempt, CURLcode result, bool exhausted)
{
    (void)exhausted;
    fprintf(stderr, "button-monitor WebSocket connect failed (attempt %zu): %s\n",
            attempt, curl_easy_strerror(result));
}

CURLcode button_websocket_connect(struct websocket_transport *connection, const char *url, int stop_fd)
{
    return websocket_transport_open_with_retry(connection, url, stop_fd,
            (struct websocket_retry_observer){.attempt_failed = report_connect_failure});
}

CURLcode button_websocket_wait(const struct websocket_transport *connection, int queued_fd, int stop_fd)
{
    struct websocket_wait_events events;
    if (websocket_transport_wait(connection, POLLIN, stop_fd, queued_fd, -1, &events) != 0) {
        return CURLE_RECV_ERROR;
    }
    if (events.stop != 0) {
        return CURLE_ABORTED_BY_CALLBACK;
    }
    // Leave the queue notification pending when this socket is already gone.
    if (events.socket & (POLLERR | POLLHUP | POLLNVAL)) {
        return CURLE_RECV_ERROR;
    }
    if (events.queued & POLLIN) {
        eventfd_t count;
        if (eventfd_read(queued_fd, &count) < 0 && errno != EAGAIN) {
            return CURLE_RECV_ERROR;
        }
    }
    return CURLE_OK;
}

CURLcode button_websocket_send(const struct websocket_transport *connection, const char *run_id, uint64_t sequence)
{
    char message[160];
    int length = snprintf(message, sizeof message,
                          "{\"type\":\"ButtonPressed\",\"nodeId\":\"mainpi\",\"runId\":\"%s\","
                          "\"payload\":{\"seq\":%" PRIu64 "}}", run_id, sequence);
    if (length < 0 || (size_t)length >= sizeof message) {
        return CURLE_SEND_ERROR;
    }

    return websocket_transport_send_text(connection, message, (size_t)length, -1);
}
