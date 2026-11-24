#ifndef PILAB_BSW_WEBSOCKET_TRANSPORT_H
#define PILAB_BSW_WEBSOCKET_TRANSPORT_H

#include <curl/curl.h>
#include <poll.h>
#include <stdbool.h>

struct websocket_transport {
    CURL *handle;
    curl_socket_t socket_fd;
};

/* A single network task owns a zero-initialized transport and all use of its
 * handle. The handle and descriptor are valid only while the transport is open. */
CURLcode websocket_transport_open(struct websocket_transport *transport, const char *url);
void websocket_transport_close(struct websocket_transport *transport);

struct websocket_retry_observer {
    void (*attempt_started)(size_t attempt, int delay_ms);
    void (*attempt_failed)(size_t attempt, CURLcode result, bool exhausted);
};

/* Open a closed transport with up to three attempts after waits of 0, 1 and
 * 2 seconds. A stop notification during a wait cancels the remaining attempts.
 * Observer callbacks are optional and run synchronously on the network task. */
CURLcode websocket_transport_open_with_retry(struct websocket_transport *transport, const char *url,
                                            int stop_fd, struct websocket_retry_observer observer);

struct websocket_wait_events {
    short socket;
    short stop;
    short queued;
};

/* Observe all ready descriptors in one poll result. Pass -1 for any unused
 * wakeup descriptor or to wait indefinitely, and 0 ms to check immediately.
 * Returns 0 with poll flags in events, ETIMEDOUT, or an errno error code.
 * The caller owns reading the descriptors and choosing what to handle first. */
int websocket_transport_wait(const struct websocket_transport *transport, short socket_interest,
                             int stop_fd, int queued_fd, int timeout_ms, struct websocket_wait_events *events);

/* Send one nonempty text message under a single I/O deadline. stop_fd is a
 * borrowed wakeup descriptor, or -1 to wait for socket readiness alone.
 * Success means the complete payload was handed to libcurl. After an error,
 * part or all of it may have gone out, so close the connection before reuse. */
CURLcode websocket_transport_send_text(const struct websocket_transport *transport,
                                       const char *text, size_t length, int stop_fd);

/* Discard currently readable frames. CURLE_OK means no data is ready,
 * and CURLE_GOT_NOTHING indicates that the peer closed the connection. */
CURLcode websocket_transport_discard_pending(const struct websocket_transport *transport);

struct websocket_text_buffer {
    char *data;
    size_t capacity;
    size_t length;
};

/* Assemble one message in the caller's buffer, with space for a trailing NUL.
 * Start with length zero. On EAGAIN, retry with the same buffer and saved length
 * on the same connection. A zero return delivers a complete message. Discard
 * partial data after any other error or when replacing the connection. */
int websocket_transport_receive_text(const struct websocket_transport *transport,
                                     struct websocket_text_buffer *message);

#endif
