#define _POSIX_C_SOURCE 200809L

#include "light_gpio_output.h"
#include "light_protocol.h"
#include "light_sensor_input.h"
#include "virtual_actuator.h"

#include <errno.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/signalfd.h>
#include <unistd.h>

static int failure(const char *stage, int error)
{
    fprintf(stderr, "light-node %s: %s\n", stage, strerror(error));
    return EXIT_FAILURE;
}

static int stop_signal_fd(void)
{
    sigset_t signals;
    if (signal(SIGPIPE, SIG_IGN) == SIG_ERR || sigemptyset(&signals) < 0 ||
        sigaddset(&signals, SIGINT) < 0 || sigaddset(&signals, SIGTERM) < 0 ||
        sigprocmask(SIG_BLOCK, &signals, NULL) < 0) {
        return -1;
    }
    return signalfd(-1, &signals, SFD_CLOEXEC);
}

static bool parse_offset(const char *text, unsigned int *offset)
{
    if (text[0] < '0' || text[0] > '9') {
        return false;
    }
    errno = 0;
    char *end;
    unsigned long value = strtoul(text, &end, 10);
    if (errno != 0 || *end != '\0' || value > UINT_MAX) {
        return false;
    }
    *offset = (unsigned int)value;
    return true;
}

enum { RUN_ID_BYTES = 16, RUN_ID_LENGTH = 2 * RUN_ID_BYTES };

static int make_run_id(char run_id[static RUN_ID_LENGTH + 1])
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
    run_id[RUN_ID_LENGTH] = '\0';
    return 0;
}

static int read_light(void *context, bool *on)
{
    return light_gpio_read(context, on);
}

static int serve_session(struct light_gpio_output *gpio, struct light_websocket *ws,
                         struct light_rte *rte, const char *run_id, struct light_ws_input *input)
{
    bool on;
    int error = light_gpio_read(gpio, &on);
    if (error != 0) {
        return failure("read initial GPIO state", error);
    }
    error = light_protocol_send_state(ws, run_id, on);
    if (error != 0) {
        return error == ECANCELED ? EXIT_SUCCESS : failure("send initial light state", error);
    }
    printf("{\"event\":\"ready\",\"on\":%s,\"pid\":%ld,\"runId\":\"%s\"}\n",
           on ? "true" : "false", (long)getpid(), run_id);

    for (;;) {
        char text[LIGHT_WS_MAX_MESSAGE + 1];
        size_t length;
        error = light_ws_receive(ws, text, &length, input);
        if (error == ECONNRESET) {
            return error;
        }
        if (error != 0) {
            return error == ECANCELED ? EXIT_SUCCESS : failure("receive light command or sample input", error);
        }
        error = light_protocol_handle(rte, text, length);
        if (error == ECONNRESET) {
            return error;
        }
        if (error != 0) {
            return error == ECANCELED ? EXIT_SUCCESS : failure("apply light command or reply", error);
        }
    }
}

static int set_blind_direction(void *context, enum blind_direction direction)
{
    return virtual_actuator_write(context, direction, false);
}

static int serve(struct light_gpio_output *gpio, struct light_websocket *ws, const char *url,
                  bool ambient_stdin, char *blind_path)
{
    // The device, run ID and applied-command record outlive either socket.
    char run_id[RUN_ID_LENGTH + 1];
    int error = make_run_id(run_id);
    if (error != 0) {
        return failure("generate run ID", error);
    }
    struct light_sensor_input sensor;
    light_sensor_input_init(&sensor, STDIN_FILENO, run_id);
    struct light_ws_input input = {.fd = STDIN_FILENO, .context = &sensor, .consume = light_sensor_consume};
    struct light_rte rte = {
        .ports = {
            .device = {.set_light = light_gpio_set, .context = gpio},
            .blind = {
                .set_direction = blind_path == NULL ? NULL : set_blind_direction,
                .context = blind_path,
            },
            .read_light = read_light,
            .send_applied = light_protocol_send_applied,
            .send_state_snapshot = light_protocol_send_snapshot,
            .send_context = ws,
        },
    };
    int status;
    for (;;) {
        status = serve_session(gpio, ws, &rte, run_id, ambient_stdin ? &input : NULL);
        if (status != ECONNRESET) {
            break;
        }
        puts("{\"event\":\"connection_lost\"}");
        error = light_ws_reconnect(ws, url);
        if (error != 0) {
            status = error == ECANCELED ? EXIT_SUCCESS : failure("reconnect WebSocket", error);
            break;
        }
    }
    light_rte_destroy(&rte);
    return status;
}

int main(int argc, char **argv)
{
    const char *usage = "usage: light-node GPIOCHIP OFFSET {off|on} WS_URL"
                        " [--ambient-stdin] [--blind-output PATH]\n";
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        fputs(usage, stdout);
        return EXIT_SUCCESS;
    }
    unsigned int offset;
    bool ambient_stdin = false;
    char *blind_path = NULL;
    for (int i = 5; i < argc; i++) {
        if (!ambient_stdin && strcmp(argv[i], "--ambient-stdin") == 0) {
            ambient_stdin = true;
        } else if (blind_path == NULL && strcmp(argv[i], "--blind-output") == 0 && i + 1 < argc) {
            blind_path = argv[++i];
        } else {
            fputs(usage, stderr);
            return EXIT_FAILURE;
        }
    }
    if (argc < 5 || !parse_offset(argv[2], &offset) ||
        (strcmp(argv[3], "off") != 0 && strcmp(argv[3], "on") != 0)) {
        fputs(usage, stderr);
        return EXIT_FAILURE;
    }
    if (setvbuf(stdout, NULL, _IOLBF, 0) != 0) {
        return failure("configure stdout", EIO);
    }
    if (blind_path != NULL) {
        int error = virtual_actuator_write(blind_path, 0, true);
        if (error != 0) {
            return failure("initialize virtual blind output", error);
        }
    }
    CURLcode result = curl_global_init(CURL_GLOBAL_DEFAULT);
    if (result != CURLE_OK) {
        fprintf(stderr, "light-node libcurl initialization: %s\n", curl_easy_strerror(result));
        return EXIT_FAILURE;
    }
    int stop_fd = stop_signal_fd();
    if (stop_fd < 0) {
        int error = errno;
        curl_global_cleanup();
        return failure("open stop signal", error);
    }

    struct light_gpio_output gpio = {0};
    struct light_websocket ws = {0};
    int status;
    int error = light_ws_open(&ws, argv[4], stop_fd);
    if (error != 0) {
        status = failure("open WebSocket", error);
    } else {
        error = light_gpio_open(&gpio, argv[1], offset, strcmp(argv[3], "on") == 0);
        if (error != 0) {
            status = failure("open GPIO output", error);
        } else {
            status = serve(&gpio, &ws, argv[4], ambient_stdin, blind_path);
            light_gpio_close(&gpio);
        }
        light_ws_close(&ws);
    }
    close(stop_fd);
    curl_global_cleanup();
    return status;
}
