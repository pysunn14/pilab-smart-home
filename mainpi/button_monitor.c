#define _POSIX_C_SOURCE 200809L

#include "button_gpio_input.h"
#include "button_input_linux.h"
#include "button_rte.h"
#include "button_tx_queue.h"
#include "button_websocket.h"

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/random.h>
#include <sys/signalfd.h>
#include <time.h>
#include <unistd.h>

static const uint64_t ns_per_ms = UINT64_C(1000000);
enum { RUN_ID_BYTES = 16 };
#ifndef BUTTON_MONITOR_HEARTBEAT_NS
#define BUTTON_MONITOR_HEARTBEAT_NS UINT64_C(30000000000)
#endif

struct monitor {
    struct button_input_linux input;
    struct button_input_event first_edge;
    bool first_edge_pending;
    uint64_t heartbeat_deadline_ns;
    struct button_tx_queue tx_queue;
    int tx_queued_fd;
    int tx_stop_fd;
    uint64_t next_sequence;
    int publish_error;
    int tx_error;
    const char *websocket_url;
    char run_id[2 * RUN_ID_BYTES + 1];
};

struct button_output_context {
    struct monitor *monitor;
    uint64_t edge_ns;
};

static int report_error(const char *stage, int error)
{
    fprintf(stderr, "button-monitor %s error (%d) %s\n", stage, error, strerror(error));
    return EXIT_FAILURE;
}

static int make_run_id(char run_id[static 2 * RUN_ID_BYTES + 1])
{
    unsigned char bytes[RUN_ID_BYTES];
    ssize_t received;
    do {
        received = getrandom(bytes, sizeof bytes, 0);
    } while (received < 0 && errno == EINTR);
    if (received != sizeof bytes) {
        return received < 0 ? errno : EIO;
    }

    const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof bytes; i++) {
        run_id[2 * i] = hex[bytes[i] >> 4];
        run_id[2 * i + 1] = hex[bytes[i] & 0xf];
    }
    run_id[2 * sizeof bytes] = '\0';
    return 0;
}

static bool read_monotonic_ns(uint64_t *time_ns)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
        return false;
    }
    *time_ns = (uint64_t)now.tv_sec * 1000 * ns_per_ms + (uint64_t)now.tv_nsec;
    return true;
}

static int monitor_wait(void *context, const uint64_t *deadline_ns,
                        struct button_input_event *event)
{
    struct monitor *monitor = context;
    if (monitor->publish_error != 0) {
        return monitor->publish_error;
    }
    if (monitor->first_edge_pending) {
        *event = monitor->first_edge;
        monitor->first_edge_pending = false;
        return 0;
    }

    for (;;) {
        uint64_t now_ns;
        if (!read_monotonic_ns(&now_ns)) {
            return errno;
        }
        if (now_ns >= monitor->heartbeat_deadline_ns) {
            puts("{\"event\":\"heartbeat\"}");
            monitor->heartbeat_deadline_ns = now_ns + BUTTON_MONITOR_HEARTBEAT_NS;
        }

        // A heartbeat may wake this wait before the requested debounce deadline.
        const uint64_t *wake_at = &monitor->heartbeat_deadline_ns;
        if (deadline_ns != NULL && *deadline_ns <= *wake_at) {
            wake_at = deadline_ns;
        }
        int error = button_input_linux_wait(&monitor->input, wake_at, event);
        if (error != 0 || event->kind != BUTTON_INPUT_TIMER || wake_at == deadline_ns) {
            return error;
        }
    }
}

static int signal_tx_fd(int fd)
{
    int result;
    do {
        result = eventfd_write(fd, 1);
    } while (result < 0 && errno == EINTR);
    return result == 0 || errno == EAGAIN ? 0 : errno;
}

static int enqueue_button_pressed(void *context)
{
    const struct button_output_context *output = context;
    struct monitor *monitor = output->monitor;
    uint64_t sequence = ++monitor->next_sequence;
    printf("{\"event\":\"button_pressed\",\"type\":\"ButtonPressed\",\"seq\":%" PRIu64
           ",\"edge_ns\":%" PRIu64 "}\n", sequence, output->edge_ns);
    int error = button_tx_queue_push(&monitor->tx_queue, sequence);
    if (error != 0) {
        printf("{\"event\":\"button_tx_rejected\",\"seq\":%" PRIu64 ",\"error\":%d}\n",
               sequence, error);
    }
    return error == 0 && monitor->tx_queued_fd >= 0 ? signal_tx_fd(monitor->tx_queued_fd) : error;
}

static void publish_transition(void *context, enum button_transition transition, uint64_t edge_ns)
{
    struct monitor *monitor = context;
    struct button_output_context output = {.monitor = monitor, .edge_ns = edge_ns};
    const struct button_rte_ports ports = {
        .send_button_pressed = enqueue_button_pressed,
        .context = &output,
    };
    int error = button_rte_receive_button_state_changed(&ports, transition == BUTTON_PRESSED);
    if (error != 0 && error != ENOSPC) {
        monitor->publish_error = error;
    }
    if (transition == BUTTON_RELEASED) {
        puts("{\"event\":\"button_released\"}");
    }
}

static int run_local_tx(struct monitor *monitor)
{
    uint64_t sequence;
    int error;
    while ((error = button_tx_queue_wait_pop(&monitor->tx_queue, &sequence)) == 0) {
        if (printf("{\"event\":\"button_tx_dequeued\",\"seq\":%" PRIu64 "}\n", sequence) < 0) {
            return errno;
        }
    }
    return error;
}

static int transmit_on_connection(struct monitor *monitor, const struct websocket_transport *connection)
{
    for (;;) {
        // A readable close can arrive before or together with the next button.
        if (websocket_transport_discard_pending(connection) != CURLE_OK) {
            return ECONNRESET;
        }
        uint64_t sequence;
        int error = button_tx_queue_pop(&monitor->tx_queue, &sequence);
        if (error == EAGAIN) {
            CURLcode result = button_websocket_wait(connection, monitor->tx_queued_fd, monitor->tx_stop_fd);
            if (result == CURLE_OK) {
                continue;
            }
            return result == CURLE_ABORTED_BY_CALLBACK ? ECANCELED : ECONNRESET;
        }
        if (error != 0) {
            return error;
        }
        if (printf("{\"event\":\"button_tx_dequeued\",\"seq\":%" PRIu64 "}\n", sequence) < 0) {
            return errno;
        }
        CURLcode result = button_websocket_send(connection, monitor->run_id, sequence);
        if (result != CURLE_OK) {
            // The peer may already have received the frame. Preserve the later queue entries.
            fprintf(stderr, "button-monitor WebSocket delivery uncertain for seq %" PRIu64 ": %s\n",
                    sequence, curl_easy_strerror(result));
            return ECONNRESET;
        }
        printf("{\"event\":\"button_tx_sent\",\"seq\":%" PRIu64 "}\n", sequence);
    }
}

static int run_websocket_tx(struct monitor *monitor)
{
    for (;;) {
        struct websocket_transport connection = {0};
        CURLcode result = button_websocket_connect(&connection, monitor->websocket_url, monitor->tx_stop_fd);
        if (result != CURLE_OK) {
            return result == CURLE_ABORTED_BY_CALLBACK ? ECANCELED : EIO;
        }
        fprintf(stderr, "button-monitor WebSocket connected\n");
        int error = transmit_on_connection(monitor, &connection);
        websocket_transport_close(&connection);
        if (error != ECONNRESET) {
            return error;
        }
        fprintf(stderr, "button-monitor WebSocket disconnected\n");
    }
}

static void *run_tx_task(void *context)
{
    struct monitor *monitor = context;
    int error = monitor->websocket_url == NULL ? run_local_tx(monitor) : run_websocket_tx(monitor);
    if (error != ECANCELED) {
        monitor->tx_error = error;
        report_error("transmission task", error);
        /* A process-directed signal wakes the input task's existing signalfd. */
        if (kill(getpid(), SIGTERM) < 0) {
            exit(report_error("wake input task", errno));
        }
    }
    return NULL;
}

static int run_input_task(struct monitor *monitor, uint64_t debounce_ns)
{
    int error = monitor_wait(monitor, NULL, &monitor->first_edge);
    if (error != 0 || monitor->first_edge.kind == BUTTON_INPUT_STOP) {
        return error;
    }

    /* The first queued edge can predate the startup level sample. Its
     * direction gives the preceding level for replaying the edge stream.
     */
    monitor->first_edge_pending = true;
    const struct button_input_io io = {.wait = monitor_wait, .publish = publish_transition};
    return button_input_run(&io, monitor, !monitor->first_edge.gpio_high, debounce_ns);
}

static void close_tx_fds(struct monitor *monitor)
{
    if (monitor->tx_queued_fd >= 0) {
        close(monitor->tx_queued_fd);
    }
    if (monitor->tx_stop_fd >= 0) {
        close(monitor->tx_stop_fd);
    }
}

static int run_monitor(struct button_gpio_input *gpio, int stop_fd, uint64_t debounce_ns,
                       const char *websocket_url)
{
    bool gpio_high;
    int error = button_gpio_read_level(gpio, &gpio_high);
    if (error != 0) {
        return error;
    }
    struct monitor monitor = {
        .websocket_url = websocket_url,
        .tx_queued_fd = -1,
        .tx_stop_fd = -1,
        .input = {
            .edge_fd = button_gpio_fd(gpio),
            .stop_fd = stop_fd,
            .read_edge = button_gpio_read_edge,
            .edge_context = gpio,
        },
    };
    if (monitor.input.edge_fd < 0) {
        return EBADF;
    }
    if (websocket_url != NULL) {
        /* An event keeps its identity if its transport connection changes. */
        error = make_run_id(monitor.run_id);
        if (error != 0) {
            fprintf(stderr, "button-monitor could not generate run ID: %s\n", strerror(error));
            return error;
        }
    }
    uint64_t now_ns;
    if (!read_monotonic_ns(&now_ns)) {
        return errno;
    }
    monitor.heartbeat_deadline_ns = now_ns + BUTTON_MONITOR_HEARTBEAT_NS;
    uint64_t tx_slots[16];
    error = button_tx_queue_init(&monitor.tx_queue, tx_slots, sizeof tx_slots / sizeof tx_slots[0]);
    if (error != 0) {
        return error;
    }
    if (websocket_url != NULL) {
        monitor.tx_queued_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        if (monitor.tx_queued_fd >= 0) {
            monitor.tx_stop_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
        }
        if (monitor.tx_stop_fd < 0) {
            error = errno;
            close_tx_fds(&monitor);
            button_tx_queue_destroy(&monitor.tx_queue);
            return error;
        }
    }
    pthread_t tx_thread;
    error = pthread_create(&tx_thread, NULL, run_tx_task, &monitor);
    if (error != 0) {
        close_tx_fds(&monitor);
        button_tx_queue_destroy(&monitor.tx_queue);
        return error;
    }
    printf("{\"event\":\"ready\",\"gpio_high\":%s}\n", gpio_high ? "true" : "false");

    error = run_input_task(&monitor, debounce_ns);
    int stop_error = button_tx_queue_close(&monitor.tx_queue);
    if (stop_error == 0 && monitor.tx_stop_fd >= 0) {
        stop_error = signal_tx_fd(monitor.tx_stop_fd);
    }
    if (stop_error == 0) {
        stop_error = pthread_join(tx_thread, NULL);
    }
    if (stop_error != 0) {
        /* The worker may still reference storage owned by this stack frame. */
        exit(report_error("stop transmission task", stop_error));
    }
    close_tx_fds(&monitor);
    if (websocket_url != NULL) {
        uint64_t sequence;
        while (button_tx_queue_pop(&monitor.tx_queue, &sequence) == 0) {
            fprintf(stderr, "button-monitor WebSocket event unsent for seq %" PRIu64 "\n", sequence);
        }
    }
    int destroy_error = button_tx_queue_destroy(&monitor.tx_queue);
    if (error != 0) {
        return error;
    }
    return monitor.tx_error != 0 ? monitor.tx_error : destroy_error;
}

static int open_stop_signal(void)
{
    sigset_t signals;
    if (sigemptyset(&signals) < 0 || sigaddset(&signals, SIGINT) < 0 ||
        sigaddset(&signals, SIGTERM) < 0 || sigprocmask(SIG_BLOCK, &signals, NULL) < 0) {
        return -1;
    }
    return signalfd(-1, &signals, SFD_CLOEXEC);
}

static bool parse_uint(const char *text, unsigned int *value)
{
    if (text[0] < '0' || text[0] > '9') {
        return false;
    }
    char *end;
    errno = 0;
    uintmax_t parsed = strtoumax(text, &end, 10);
    if (errno != 0 || *end != '\0' || parsed > UINT_MAX) {
        return false;
    }
    *value = (unsigned int)parsed;
    return true;
}

int main(int argc, char **argv)
{
    const char *usage = "usage: button-monitor GPIOCHIP OFFSET DEBOUNCE_MS [WS_URL]\n";
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        fputs(usage, stdout);
        return EXIT_SUCCESS;
    }
    unsigned int offset;
    unsigned int debounce_ms;
    if ((argc != 4 && argc != 5) || !parse_uint(argv[2], &offset) ||
        !parse_uint(argv[3], &debounce_ms)) {
        fputs(usage, stderr);
        return EXIT_FAILURE;
    }
    if (setvbuf(stdout, NULL, _IOLBF, 0) != 0) {
        return report_error("configure stdout", EIO);
    }

    const char *websocket_url = argc == 5 ? argv[4] : NULL;
    /* With CURLOPT_NOSIGNAL, a broken connection must be handled as an error
     * from libcurl instead of terminating this process with SIGPIPE.
     */
    if (websocket_url != NULL && signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
        return report_error("configure SIGPIPE", errno);
    }
    int stop_fd = open_stop_signal();
    if (stop_fd < 0) {
        return report_error("open stop signal", errno);
    }
    struct button_gpio_input gpio = {0};
    int error = button_gpio_open(&gpio, argv[1], offset);
    if (error != 0) {
        close(stop_fd);
        return report_error("open GPIO", error);
    }

    if (websocket_url != NULL) {
        CURLcode result = curl_global_init(CURL_GLOBAL_DEFAULT);
        if (result != CURLE_OK) {
            fprintf(stderr, "button-monitor libcurl initialization failed %s\n", curl_easy_strerror(result));
            button_gpio_close(&gpio);
            close(stop_fd);
            return EXIT_FAILURE;
        }
    }
    error = run_monitor(&gpio, stop_fd, (uint64_t)debounce_ms * ns_per_ms, websocket_url);
    if (websocket_url != NULL) {
        curl_global_cleanup();
    }
    button_gpio_close(&gpio);
    close(stop_fd);
    if (error != 0) {
        return report_error("run monitor", error);
    }
    puts("{\"event\":\"stopped\"}");
    return EXIT_SUCCESS;
}
