#define _POSIX_C_SOURCE 200809L
#include "virtual_actuator.h"

#include <errno.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int virtual_actuator_write(const char *path, int value, bool initialize)
{
    char temporary[PATH_MAX];
    int length = snprintf(temporary, sizeof temporary, "%s.XXXXXX", path);
    if (length < 0 || (size_t)length >= sizeof temporary) {
        return ENAMETOOLONG;
    }
    int fd = mkstemp(temporary);
    if (fd < 0) {
        return errno;
    }
    char encoded[32], observed[32];
    length = snprintf(encoded, sizeof encoded, "%d\n", value);
    int error = 0;
    if (length < 1 || (size_t)length >= sizeof encoded || write(fd, encoded, (size_t)length) != length
            || pread(fd, observed, (size_t)length, 0) != length || memcmp(observed, encoded, (size_t)length) != 0) {
        error = EIO;
    }
    if (close(fd) < 0 && error == 0) {
        error = errno;
    }
    if (error == 0) {
        // Initial creation must not take over another producer's file.
        int published = initialize ? link(temporary, path) : rename(temporary, path);
        if (published < 0) {
            error = errno;
        }
    }
    (void)unlink(temporary);
    return error;
}

static int read_output(const char *path, int max)
{
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        return -1;
    }
    struct stat output;
    char value[8] = {0};
    ssize_t size = fstat(fd, &output) == 0 && S_ISREG(output.st_mode)
        ? read(fd, value, sizeof value - 1) : -1;
    close(fd);
    char *end;
    long number = strtol(value, &end, 10);
    if (size < 2 || size > 4 || strspn(value, "0123456789") != (size_t)(size - 1)
            || end != value + size - 1 || *end != '\n' || number < 0 || number > max) {
        return -1;
    }
    return (int)number;
}

CURLcode virtual_actuator_report(const struct virtual_actuator_protocol *protocol, const char *path,
                                 struct websocket_transport *transport)
{
    int value = read_output(path, protocol->kind == VIRTUAL_SWITCH ? 1 : 180);
    if (value < 0) {
        fprintf(stderr, "%s: cannot report virtual output %s\n", protocol->node_id, protocol->field);
        return CURLE_OK;
    }
    char number[16];
    snprintf(number, sizeof number, "%u", (unsigned)value);
    const char *encoded = number;
    if (protocol->kind == VIRTUAL_SWITCH) {
        encoded = value ? "true" : "false";
    }
    char message[128];
    int size = snprintf(message, sizeof message,
                        "{\"type\":\"%s\",\"nodeId\":\"%s\",\"payload\":{\"%s\":%s}}",
                        protocol->state_type, protocol->node_id, protocol->field, encoded);
    return size > 0 && (size_t)size < sizeof message
        ? websocket_transport_send_text(transport, message, (size_t)size, -1) : CURLE_BAD_FUNCTION_ARGUMENT;
}

static const char *text_field(struct json_object *object, const char *name)
{
    struct json_object *field;
    if (!json_object_object_get_ex(object, name, &field) || !json_object_is_type(field, json_type_string)) {
        return "";
    }
    const char *text = json_object_get_string(field);
    return strlen(text) == (size_t)json_object_get_string_len(field) ? text : "";
}

static bool valid_id(const char *id)
{
    if (strlen(id) != 36) {
        return false;
    }
    for (size_t i = 0; i < 36; i++) {
        bool separator = i == 8 || i == 13 || i == 18 || i == 23;
        if (separator ? id[i] != '-' : strchr("0123456789abcdef", id[i]) == NULL) {
            return false;
        }
    }
    return true;
}

static bool target_value(struct json_object *field, enum virtual_actuator_value kind, int *value)
{
    if (kind == VIRTUAL_SWITCH) {
        if (!json_object_is_type(field, json_type_boolean)) {
            return false;
        }
        *value = json_object_get_boolean(field);
        return true;
    }
    if (!json_object_is_type(field, json_type_int)) {
        return false;
    }
    int64_t angle = json_object_get_int64(field);
    if (angle < 0 || angle > 180) {
        return false;
    }
    *value = (int)angle;
    return true;
}

CURLcode virtual_actuator_handle(const struct virtual_actuator_protocol *protocol, const char *path,
                                 struct websocket_transport *transport, const char *text, size_t length)
{
    struct json_tokener *parser = json_tokener_new();
    if (parser == NULL) {
        return CURLE_OUT_OF_MEMORY;
    }
    json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
    struct json_object *message = json_tokener_parse_ex(parser, text, (int)length);
    bool valid = json_tokener_get_error(parser) == json_tokener_success &&
                 json_tokener_get_parse_end(parser) == length && json_object_is_type(message, json_type_object);
    json_tokener_free(parser);

    struct json_object *payload, *target;
    int value;
    if (!valid || strcmp(text_field(message, "type"), protocol->command_type) != 0
            || strcmp(text_field(message, "nodeId"), protocol->node_id) != 0
            || !json_object_object_get_ex(message, "payload", &payload) || !json_object_is_type(payload, json_type_object)
            || !json_object_object_get_ex(payload, protocol->field, &target) || !target_value(target, protocol->kind, &value)
            || !valid_id(text_field(payload, "commandId"))) {
        fprintf(stderr, "%s: ignored invalid %s\n", protocol->node_id, protocol->command_type);
        json_object_put(message);
        return CURLE_OK;
    }

    const char *id = text_field(payload, "commandId");
    const char *encoded = json_object_to_json_string_ext(target, JSON_C_TO_STRING_PLAIN);
    if (encoded == NULL) {
        json_object_put(message);
        return CURLE_OUT_OF_MEMORY;
    }
    int error = virtual_actuator_write(path, value, false);
    if (error != 0) {
        fprintf(stderr, "%s: output unconfirmed for %s: %s\n", protocol->node_id, id, strerror(error));
    } else {
        printf("{\"event\":\"%s\",\"commandId\":\"%s\",\"%s\":%s}\n",
               protocol->applied_event, id, protocol->field, encoded);
        fflush(stdout);
    }
    char reply[192];
    int size = snprintf(reply, sizeof reply,
                        "{\"type\":\"%s%s\",\"nodeId\":\"%s\",\"payload\":{\"commandId\":\"%s\",\"%s\":%s}}",
                        protocol->command_type, error == 0 ? "Applied" : "Unconfirmed",
                        protocol->node_id, id, protocol->field, encoded);
    json_object_put(message);
    return size > 0 && (size_t)size < sizeof reply
        ? websocket_transport_send_text(transport, reply, (size_t)size, -1) : CURLE_BAD_FUNCTION_ARGUMENT;
}
