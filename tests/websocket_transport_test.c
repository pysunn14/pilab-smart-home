#include "websocket_transport.h"

#include <assert.h>
#include <errno.h>
#include <poll.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#ifdef __linux__
#include "button_websocket.h"

#include <sys/eventfd.h>
#endif

static CURLcode expected_connect_error;
static size_t reported_attempts;
static size_t reported_failures;
static bool last_exhausted;
static int cancel_after_failure = -1;

static void observe_attempt(size_t attempt, int delay_ms)
{
    const int expected_delays[] = {0, 1000, 2000};
    assert(attempt == ++reported_attempts && attempt <= 3);
    assert(delay_ms == expected_delays[attempt - 1]);
}

static void observe_failure(size_t attempt, CURLcode result, bool exhausted)
{
    assert(attempt == ++reported_failures && attempt == reported_attempts);
    assert(result == expected_connect_error && exhausted == (attempt == 3));
    last_exhausted = exhausted;
    if (cancel_after_failure >= 0) {
        assert(write(cancel_after_failure, "x", 1) == 1);
    }
}

static void test_connection_retry_limit_and_cancel(void)
{
    int stop[2];
    assert(pipe(stop) == 0);
    struct websocket_transport transport = {0};
    const char *invalid_url = "://invalid";
    expected_connect_error = websocket_transport_open(&transport, invalid_url);
    assert(expected_connect_error != CURLE_OK);
    const struct websocket_retry_observer observer = {
        .attempt_started = observe_attempt,
        .attempt_failed = observe_failure,
    };

    assert(websocket_transport_open_with_retry(&transport, invalid_url, stop[0], observer)
           == expected_connect_error);
    assert(reported_attempts == 3 && reported_failures == 3 && last_exhausted);
    assert(transport.handle == NULL);

    reported_attempts = reported_failures = 0;
    last_exhausted = false;
    cancel_after_failure = stop[1];
    assert(websocket_transport_open_with_retry(&transport, invalid_url, stop[0], observer)
           == CURLE_ABORTED_BY_CALLBACK);
    assert(reported_attempts == 1 && reported_failures == 1 && !last_exhausted);
    assert(transport.handle == NULL);
    close(stop[0]);
    close(stop[1]);
}

enum { PARTIAL_SEND, BLOCKED_SEND };
static int scenario;
static unsigned int calls;

CURLcode test_curl_ws_send(CURL *handle, const void *buffer, size_t length, size_t *sent,
                          curl_off_t frame_size, unsigned int flags)
{
    assert(handle != NULL);
    assert(frame_size == 0 && flags == CURLWS_TEXT);
    unsigned int call = calls++;
    *sent = 0;
    if (scenario == BLOCKED_SEND) {
        return CURLE_AGAIN;
    }
    if (call == 0) {
        assert(length == 5 && memcmp(buffer, "hello", 5) == 0);
        *sent = 2;
        return CURLE_OK;
    }
    assert(length == 3 && memcmp(buffer, "llo", 3) == 0);
    if (call == 1) {
        return CURLE_AGAIN;
    }
    assert(call == 2);
    *sent = length;
    return CURLE_OK;
}

struct receive_step {
    CURLcode result;
    struct curl_ws_frame frame;
    const char *chunk;
};

static const struct receive_step *receive_steps;
static size_t receive_count;
static size_t receive_index;

static void use_receive_steps(const struct receive_step *steps, size_t count)
{
    receive_steps = steps;
    receive_count = count;
    receive_index = 0;
}

CURLcode test_curl_ws_recv(CURL *handle, void *buffer, size_t capacity, size_t *received,
                          const struct curl_ws_frame **frame)
{
    assert(handle != NULL);
    assert(receive_index < receive_count);
    const struct receive_step *step = &receive_steps[receive_index++];
    *received = 0;
    *frame = NULL;
    if (step->result != CURLE_OK) {
        return step->result;
    }
    *received = strlen(step->chunk);
    assert(*received <= capacity);
    memcpy(buffer, step->chunk, *received);
    *frame = &step->frame;
    return CURLE_OK;
}

static void test_receive_keeps_fragments_across_wait(const struct websocket_transport *transport)
{
    const struct receive_step steps[] = {
        {.frame.flags = CURLWS_TEXT | CURLWS_CONT, .chunk = "he"},
        {.result = CURLE_AGAIN},
        {.frame.flags = CURLWS_PONG, .chunk = "ignored"},
        {.frame = {.flags = CURLWS_TEXT, .bytesleft = 1}, .chunk = "ll"},
        {.frame.flags = CURLWS_TEXT, .chunk = "o"},
        {.frame.flags = CURLWS_TEXT, .chunk = "next"},
    };
    use_receive_steps(steps, sizeof steps / sizeof steps[0]);
    char text[6];
    struct websocket_text_buffer message = {.data = text, .capacity = sizeof text};

    assert(websocket_transport_receive_text(transport, &message) == EAGAIN);
    assert(message.length == 2 && memcmp(text, "he", 2) == 0);
    assert(websocket_transport_receive_text(transport, &message) == 0);
    assert(message.length == 5 && strcmp(text, "hello") == 0);
    assert(receive_index == 5);

    message.length = 0;
    assert(websocket_transport_receive_text(transport, &message) == 0);
    assert(message.length == 4 && strcmp(text, "next") == 0);
}

static void test_receive_reports_closed_binary_and_oversize(const struct websocket_transport *transport)
{
    const struct receive_step closing[] = {
        {.frame.flags = CURLWS_TEXT | CURLWS_CONT, .chunk = "part"},
        {.frame.flags = CURLWS_CLOSE, .chunk = ""},
    };
    const struct receive_step binary[] = {{.frame.flags = CURLWS_BINARY, .chunk = "binary"}};
    const struct receive_step oversize[] = {
        {.frame = {.flags = CURLWS_TEXT, .bytesleft = 5}, .chunk = "x"},
    };
    char text[6];
    struct websocket_text_buffer message = {.data = text, .capacity = sizeof text};

    use_receive_steps(closing, sizeof closing / sizeof closing[0]);
    assert(websocket_transport_receive_text(transport, &message) == ECONNRESET);

    message.length = 0;
    use_receive_steps(binary, 1);
    assert(websocket_transport_receive_text(transport, &message) == EPROTO);
    use_receive_steps(oversize, 1);
    assert(websocket_transport_receive_text(transport, &message) == EMSGSIZE);
}

static void test_discard_pending_stops_at_empty_or_closed(const struct websocket_transport *transport)
{
    const struct receive_step pending[] = {
        {.frame = {.flags = CURLWS_TEXT, .bytesleft = 3}, .chunk = "part"},
        {.frame.flags = CURLWS_TEXT, .chunk = "end"},
        {.frame.flags = CURLWS_BINARY, .chunk = "binary"},
        {.result = CURLE_AGAIN},
        {.frame.flags = CURLWS_PONG, .chunk = "pong"},
        {.frame.flags = CURLWS_CLOSE, .chunk = ""},
    };
    use_receive_steps(pending, sizeof pending / sizeof pending[0]);
    assert(websocket_transport_discard_pending(transport) == CURLE_OK);
    assert(receive_index == 4);
    assert(websocket_transport_discard_pending(transport) == CURLE_GOT_NOTHING);
    assert(receive_index == 6);

    const struct receive_step rejected[] = {{.result = CURLE_RECV_ERROR}};
    use_receive_steps(rejected, 1);
    assert(websocket_transport_discard_pending(transport) == CURLE_RECV_ERROR);
}

static void test_wait_reports_ready_descriptors(const struct websocket_transport *transport, int peer)
{
    int stop[2];
    int queued[2];
    assert(pipe(stop) == 0);
    assert(pipe(queued) == 0);

    struct websocket_wait_events events;
    assert(websocket_transport_wait(transport, POLLIN, stop[0], queued[0], 0, &events) == ETIMEDOUT);
    assert(websocket_transport_wait(transport, POLLIN, stop[0], queued[0], 5, &events) == ETIMEDOUT);

    assert(write(peer, "s", 1) == 1);
    assert(write(stop[1], "x", 1) == 1);
    assert(write(queued[1], "q", 1) == 1);
    assert(websocket_transport_wait(transport, POLLIN, stop[0], queued[0], -1, &events) == 0);
    assert(events.socket & POLLIN);
    assert(events.stop & POLLIN);
    assert(events.queued & POLLIN);

    char signal;
    assert(read(stop[0], &signal, 1) == 1 && signal == 'x');
    assert(read(queued[0], &signal, 1) == 1 && signal == 'q');
    assert(read(transport->socket_fd, &signal, 1) == 1 && signal == 's');
    close(stop[0]);
    close(stop[1]);
    close(queued[0]);
    close(queued[1]);
}

#ifdef __linux__
static void test_mainpi_keeps_queue_notification_when_socket_closes(
    const struct websocket_transport *transport)
{
    int queued = eventfd(1, EFD_NONBLOCK | EFD_CLOEXEC);
    assert(queued >= 0);
    struct websocket_wait_events events;
    assert(websocket_transport_wait(transport, POLLIN, -1, queued, -1, &events) == 0);
    assert(events.socket & POLLHUP);
    assert(events.queued & POLLIN);
    assert(button_websocket_wait(transport, queued, -1) == CURLE_RECV_ERROR);
    eventfd_t remaining;
    assert(eventfd_read(queued, &remaining) == 0 && remaining == 1);
    close(queued);
}
#endif

int main(void)
{
    int sockets[2];
    int stop[2];
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0);
    assert(pipe(stop) == 0);

    assert(curl_global_init(CURL_GLOBAL_DEFAULT) == CURLE_OK);
    test_connection_retry_limit_and_cancel();
    struct websocket_transport transport = {.handle = curl_easy_init(), .socket_fd = sockets[0]};
    assert(transport.handle != NULL);

    scenario = PARTIAL_SEND;
    assert(websocket_transport_send_text(&transport, "hello", 5, -1) == CURLE_OK);
    assert(calls == 3);

    scenario = BLOCKED_SEND;
    calls = 0;
    assert(write(stop[1], "x", 1) == 1);
    assert(websocket_transport_send_text(&transport, "hello", 5, stop[0]) == CURLE_ABORTED_BY_CALLBACK);
    assert(calls == 1);

    test_receive_keeps_fragments_across_wait(&transport);
    test_receive_reports_closed_binary_and_oversize(&transport);
    test_discard_pending_stops_at_empty_or_closed(&transport);
    test_wait_reports_ready_descriptors(&transport, sockets[1]);
    close(sockets[1]);
#ifdef __linux__
    test_mainpi_keeps_queue_notification_when_socket_closes(&transport);
#endif
    websocket_transport_close(&transport);
    websocket_transport_close(&transport);
    assert(transport.handle == NULL && transport.socket_fd == CURL_SOCKET_BAD);
    close(stop[0]);
    close(stop[1]);
    close(sockets[0]);
    curl_global_cleanup();
}
