#define _POSIX_C_SOURCE 200809L

#include "light_protocol.h"

#include <errno.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int __real_light_protocol_send_applied(void *context, const struct light_command *command);

static int wait_for_release(int gate, int stop_fd)
{
    struct pollfd descriptors[] = {{.fd = stop_fd, .events = POLLIN}, {.fd = gate, .events = POLLIN}};
    int status;
    do {
        status = poll(descriptors, 2, -1);
    } while (status < 0 && errno == EINTR);
    if (status < 0) {
        return errno;
    }
    if (descriptors[0].revents & POLLIN) {
        return ECANCELED;
    }
    char token;
    return read(gate, &token, 1) == 1 && token == '1' ? 0 : EIO;
}

static int log_ack(const char *event, const char *command_id)
{
    struct json_object *id = json_object_new_string(command_id);
    if (id == NULL) {
        return ENOMEM;
    }
    const char *quoted_id = json_object_to_json_string_ext(id, JSON_C_TO_STRING_NOSLASHESCAPE);
    if (quoted_id != NULL) {
        printf("{\"event\":\"%s\",\"commandId\":%s}\n", event, quoted_id);
    }
    int error = quoted_id == NULL ? ENOMEM : 0;
    json_object_put(id);
    return error;
}

int __wrap_light_protocol_send_applied(void *context, const struct light_command *command)
{
    static int dropped;
    const char *drop_count = getenv("PILAB_DROP_LIGHT_ACKS");
    if (drop_count != NULL && strcmp(drop_count, "0") != 0) {
        if (strcmp(drop_count, "1") != 0 && strcmp(drop_count, "2") != 0) {
            return EINVAL;
        }
        if (dropped < drop_count[0] - '0') {
            ++dropped;
            return log_ack("ack_dropped", command->command_id);
        }
        return __real_light_protocol_send_applied(context, command);
    }

    const char *hold = getenv("PILAB_HOLD_LIGHT_ACK");
    if (hold != NULL && strcmp(hold, "0") == 0) {
        return __real_light_protocol_send_applied(context, command);
    }

    static bool first_held;
    if (!first_held) {
        const char *path = "/board/ack-release";
        if (mkfifo(path, 0600) < 0) {
            return errno;
        }
        int gate = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (gate < 0) {
            return errno;
        }
        int error = log_ack("ack_held", command->command_id);
        if (error == 0) {
            const struct light_websocket *ws = context;
            error = wait_for_release(gate, ws->stop_fd);
        }
        close(gate);
        if (error != 0) {
            return error;
        }
        first_held = true;
        puts("{\"event\":\"ack_released\"}");
    }
    return __real_light_protocol_send_applied(context, command);
}
