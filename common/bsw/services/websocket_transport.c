#define _POSIX_C_SOURCE 200809L

#include "websocket_transport.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

enum { io_timeout_ms = 2000, receive_chunk_size = 512 };

CURLcode websocket_transport_open(struct websocket_transport *transport, const char *url)
{
    if (transport->handle != NULL) {
        return CURLE_FAILED_INIT;
    }
    CURL *handle = curl_easy_init();
    if (handle == NULL) {
        return CURLE_OUT_OF_MEMORY;
    }

    CURLcode result = curl_easy_setopt(handle, CURLOPT_PROTOCOLS_STR, "ws,wss");
    if (result == CURLE_OK) {
        result = curl_easy_setopt(handle, CURLOPT_URL, url);
    }
    if (result == CURLE_OK) {
        result = curl_easy_setopt(handle, CURLOPT_CONNECT_ONLY, 2L);
    }
    if (result == CURLE_OK) {
        result = curl_easy_setopt(handle, CURLOPT_NOSIGNAL, 1L);
    }
    if (result == CURLE_OK) {
        result = curl_easy_setopt(handle, CURLOPT_TIMEOUT_MS, (long)io_timeout_ms);
    }
    if (result == CURLE_OK) {
        result = curl_easy_perform(handle);
    }

    curl_socket_t socket_fd = CURL_SOCKET_BAD;
    if (result == CURLE_OK) {
        result = curl_easy_getinfo(handle, CURLINFO_ACTIVESOCKET, &socket_fd);
        if (result == CURLE_OK && socket_fd == CURL_SOCKET_BAD) {
            result = CURLE_COULDNT_CONNECT;
        }
    }
    if (result != CURLE_OK) {
        curl_easy_cleanup(handle);
        return result;
    }

    *transport = (struct websocket_transport){.handle = handle, .socket_fd = socket_fd};
    return CURLE_OK;
}

void websocket_transport_close(struct websocket_transport *transport)
{
    if (transport->handle != NULL) {
        curl_easy_cleanup(transport->handle);
    }
    *transport = (struct websocket_transport){.socket_fd = CURL_SOCKET_BAD};
}

static int64_t monotonic_ms(void)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
        return -1;
    }
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int wait_until(curl_socket_t socket_fd, short socket_interest, int stop_fd, int queued_fd,
                      int64_t deadline_ms, struct websocket_wait_events *events)
{
    enum { SOCKET, STOP, QUEUE };
    struct pollfd descriptors[] = {
        [SOCKET] = {.fd = socket_fd, .events = socket_interest},
        [STOP] = {.fd = stop_fd, .events = POLLIN},
        [QUEUE] = {.fd = queued_fd, .events = POLLIN},
    };
    for (;;) {
        int timeout = deadline_ms == 0 ? 0 : -1;
        if (deadline_ms > 0) {
            int64_t now = monotonic_ms();
            if (now < 0) {
                return errno;
            }
            if (now >= deadline_ms) {
                return ETIMEDOUT;
            }
            timeout = (int)(deadline_ms - now);
        }
        int ready = poll(descriptors, sizeof descriptors / sizeof descriptors[0], timeout);
        if (ready > 0) {
            *events = (struct websocket_wait_events){
                .socket = descriptors[SOCKET].revents,
                .stop = descriptors[STOP].revents,
                .queued = descriptors[QUEUE].revents,
            };
            return 0;
        }
        if (ready == 0) {
            return ETIMEDOUT;
        }
        if (errno != EINTR || deadline_ms == 0) {
            return errno;
        }
    }
}

static int wait_for(curl_socket_t socket_fd, short socket_interest, int stop_fd, int queued_fd,
                    int timeout_ms, struct websocket_wait_events *events)
{
    int64_t deadline = timeout_ms;
    if (timeout_ms > 0) {
        int64_t now = monotonic_ms();
        if (now < 0) {
            return errno;
        }
        deadline = now + timeout_ms;
    }
    return wait_until(socket_fd, socket_interest, stop_fd, queued_fd, deadline, events);
}

int websocket_transport_wait(const struct websocket_transport *transport, short socket_interest,
                             int stop_fd, int queued_fd, int timeout_ms, struct websocket_wait_events *events)
{
    if (transport->handle == NULL) {
        return ENOTCONN;
    }
    if (timeout_ms < -1) {
        return EINVAL;
    }
    return wait_for(transport->socket_fd, socket_interest, stop_fd, queued_fd, timeout_ms, events);
}

CURLcode websocket_transport_open_with_retry(struct websocket_transport *transport, const char *url,
                                            int stop_fd, struct websocket_retry_observer observer)
{
    if (transport->handle != NULL) {
        return CURLE_FAILED_INIT;
    }
    static const int delays_ms[] = {0, 1000, 2000};
    const size_t max_attempts = sizeof delays_ms / sizeof delays_ms[0];
    CURLcode result = CURLE_COULDNT_CONNECT;
    for (size_t attempt = 1; attempt <= max_attempts; ++attempt) {
        int delay = delays_ms[attempt - 1];
        struct websocket_wait_events events;
        int error = wait_for(CURL_SOCKET_BAD, 0, stop_fd, -1, delay, &events);
        if (error == 0) {
            // Only the stop descriptor participates while the transport is closed.
            return CURLE_ABORTED_BY_CALLBACK;
        }
        if (error != ETIMEDOUT) {
            return CURLE_RECV_ERROR;
        }

        if (observer.attempt_started != NULL) {
            observer.attempt_started(attempt, delay);
        }
        result = websocket_transport_open(transport, url);
        if (result == CURLE_OK) {
            return result;
        }
        if (observer.attempt_failed != NULL) {
            observer.attempt_failed(attempt, result, attempt == max_attempts);
        }
    }
    return result;
}

static CURLcode wait_writable(curl_socket_t socket_fd, int stop_fd, int64_t deadline)
{
    struct websocket_wait_events events;
    int error = wait_until(socket_fd, POLLOUT, stop_fd, -1, deadline, &events);
    if (error == ETIMEDOUT) {
        return CURLE_OPERATION_TIMEDOUT;
    }
    if (error != 0) {
        return CURLE_SEND_ERROR;
    }
    if (events.stop != 0) {
        return CURLE_ABORTED_BY_CALLBACK;
    }
    return (events.socket & POLLOUT) && !(events.socket & (POLLERR | POLLHUP | POLLNVAL))
               ? CURLE_OK : CURLE_SEND_ERROR;
}

CURLcode websocket_transport_send_text(const struct websocket_transport *transport,
                                       const char *text, size_t length, int stop_fd)
{
    if (transport->handle == NULL || text == NULL || length == 0) {
        return CURLE_BAD_FUNCTION_ARGUMENT;
    }
    int64_t started = monotonic_ms();
    if (started < 0) {
        return CURLE_SEND_ERROR;
    }

    size_t offset = 0;
    while (offset < length) {
        size_t sent = 0;
        CURLcode result = curl_ws_send(transport->handle, text + offset, length - offset, &sent, 0, CURLWS_TEXT);
        if (result != CURLE_OK && result != CURLE_AGAIN) {
            return result;
        }
        offset += sent;
        if (offset < length) {
            result = wait_writable(transport->socket_fd, stop_fd, started + io_timeout_ms);
            if (result != CURLE_OK) {
                return result;
            }
        }
    }
    return CURLE_OK;
}

static CURLcode receive_chunk(const struct websocket_transport *transport, char chunk[static receive_chunk_size],
                             size_t *received, const struct curl_ws_frame **frame)
{
    CURLcode result = curl_ws_recv(transport->handle, chunk, receive_chunk_size, received, frame);
    if (result == CURLE_OK && ((*frame)->flags & CURLWS_CLOSE)) {
        return CURLE_GOT_NOTHING;
    }
    return result;
}

CURLcode websocket_transport_discard_pending(const struct websocket_transport *transport)
{
    if (transport->handle == NULL) {
        return CURLE_RECV_ERROR;
    }

    char chunk[receive_chunk_size];
    CURLcode result;
    do {
        const struct curl_ws_frame *frame;
        size_t received;
        result = receive_chunk(transport, chunk, &received, &frame);
    } while (result == CURLE_OK);
    return result == CURLE_AGAIN ? CURLE_OK : result;
}

int websocket_transport_receive_text(const struct websocket_transport *transport,
                                     struct websocket_text_buffer *message)
{
    if (transport->handle == NULL) {
        return ENOTCONN;
    }
    if (message->data == NULL || message->length >= message->capacity) {
        return EINVAL;
    }

    char chunk[receive_chunk_size];
    for (;;) {
        const struct curl_ws_frame *frame;
        size_t received = 0;
        CURLcode result = receive_chunk(transport, chunk, &received, &frame);
        if (result == CURLE_AGAIN) {
            return EAGAIN;
        }
        if (result == CURLE_GOT_NOTHING) {
            return ECONNRESET;
        }
        if (result != CURLE_OK) {
            fprintf(stderr, "WebSocket receive: %s\n", curl_easy_strerror(result));
            return result == CURLE_OPERATION_TIMEDOUT ? ETIMEDOUT : EIO;
        }

        if (frame->flags & (CURLWS_PING | CURLWS_PONG)) {
            continue;
        }
        if (!(frame->flags & CURLWS_TEXT)) {
            return EPROTO;
        }

        size_t remaining = message->capacity - message->length - 1;
        if (received > remaining || (uintmax_t)frame->bytesleft > remaining - received) {
            return EMSGSIZE;
        }
        memcpy(message->data + message->length, chunk, received);
        message->length += received;
        if (frame->bytesleft == 0 && !(frame->flags & CURLWS_CONT)) {
            message->data[message->length] = '\0';
            return 0;
        }
    }
}
