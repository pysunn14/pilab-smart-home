#include "light_protocol.h"

#include <errno.h>
#include <json-c/json.h>
#include <stdio.h>
#include <string.h>

int light_protocol_send_state(struct light_websocket *ws, const char *run_id, bool on)
{
    char message[192];
    int length = snprintf(message, sizeof message,
                          "{\"type\":\"LightStateReported\",\"nodeId\":\"lightpi\",\"runId\":\"%s\","
                          "\"payload\":{\"on\":%s}}", run_id, on ? "true" : "false");
    if (length < 0 || (size_t)length >= sizeof message) {
        return EOVERFLOW;
    }
    return light_ws_send(ws, message);
}

int light_protocol_send_snapshot(void *context, const char *query_id, const struct light_state *state)
{
    struct json_object *query = json_object_new_string(query_id);
    if (query == NULL) {
        return ENOMEM;
    }
    struct json_object *last = NULL;
    const char *quoted_last = "null";
    if (state->last_applied_id != NULL) {
        last = json_object_new_string(state->last_applied_id);
        quoted_last = last == NULL ? NULL : json_object_to_json_string_ext(last, JSON_C_TO_STRING_NOSLASHESCAPE);
    }
    const char *quoted_query = json_object_to_json_string_ext(query, JSON_C_TO_STRING_NOSLASHESCAPE);
    char message[LIGHT_WS_MAX_MESSAGE + 1];
    int length = -1;
    if (quoted_query != NULL && quoted_last != NULL) {
        length = snprintf(message, sizeof message,
                          "{\"type\":\"LightStateSnapshot\",\"nodeId\":\"lightpi\","
                          "\"payload\":{\"queryId\":%s,\"lastAppliedCommandId\":%s,\"on\":%s}}",
                          quoted_query, quoted_last, state->on ? "true" : "false");
    }
    json_object_put(last);
    json_object_put(query);
    if (length < 0) {
        return ENOMEM;
    }
    if ((size_t)length >= sizeof message) {
        return EOVERFLOW;
    }
    return light_ws_send(context, message);
}

int light_protocol_send_applied(void *context, const struct light_command *command)
{
    struct light_websocket *ws = context;
    struct json_object *id = json_object_new_string(command->command_id);
    if (id == NULL) {
        return ENOMEM;
    }
    char message[LIGHT_WS_MAX_MESSAGE + 128];
    const char *quoted_id = json_object_to_json_string_ext(id, JSON_C_TO_STRING_NOSLASHESCAPE);
    int length = quoted_id == NULL ? -1 : snprintf(message, sizeof message,
                          "{\"type\":\"LightCommandApplied\",\"nodeId\":\"lightpi\","
                          "\"payload\":{\"commandId\":%s,\"on\":%s}}", quoted_id,
                          command->on ? "true" : "false");
    json_object_put(id);
    if (length < 0 || (size_t)length >= sizeof message) {
        return EOVERFLOW;
    }
    return light_ws_send(ws, message);
}

static bool field_equals(struct json_object *object, const char *name, const char *expected)
{
    struct json_object *field;
    return json_object_object_get_ex(object, name, &field) && json_object_is_type(field, json_type_string) &&
           (size_t)json_object_get_string_len(field) == strlen(expected) &&
           strcmp(json_object_get_string(field), expected) == 0;
}

static const char *read_id(struct json_object *payload, const char *key)
{
    struct json_object *id;
    if (!json_object_object_get_ex(payload, key, &id) || !json_object_is_type(id, json_type_string)) {
        return NULL;
    }
    const char *value = json_object_get_string(id);
    return value[0] != '\0' && strlen(value) == (size_t)json_object_get_string_len(id) ? value : NULL;
}

static int receive_blind(const struct light_rte *rte, struct json_object *payload, const char *command_id)
{
    enum blind_direction direction;
    if (field_equals(payload, "direction", "UP")) {
        direction = BLIND_UP;
    } else if (field_equals(payload, "direction", "DOWN")) {
        direction = BLIND_DOWN;
    } else {
        return EPROTO;
    }
    const char *target = direction == BLIND_UP ? "UP" : "DOWN";
    struct json_object *id = json_object_new_string(command_id);
    if (id == NULL) {
        return ENOMEM;
    }
    const char *quoted_id = json_object_to_json_string_ext(id, JSON_C_TO_STRING_NOSLASHESCAPE);
    if (quoted_id == NULL) {
        json_object_put(id);
        return ENOMEM;
    }
    int error = light_rte_receive_blind(rte, direction);
    if (error == 0) {
        printf("{\"event\":\"blind_output_applied\",\"commandId\":%s,\"direction\":\"%s\"}\n", quoted_id, target);
    } else {
        fprintf(stderr, "light-node blind output unconfirmed for %s: %s\n", quoted_id, strerror(error));
    }
    char message[LIGHT_WS_MAX_MESSAGE + 128];
    int length = snprintf(message, sizeof message,
                          "{\"type\":\"BlindCommand%s\",\"nodeId\":\"lightpi\","
                          "\"payload\":{\"commandId\":%s,\"direction\":\"%s\"}}",
                          error == 0 ? "Applied" : "Unconfirmed", quoted_id, target);
    json_object_put(id);
    if (length < 0 || (size_t)length >= sizeof message) {
        return EOVERFLOW;
    }
    return light_ws_send(rte->ports.send_context, message);
}

static int receive_message(struct light_rte *rte, struct json_object *message)
{
    struct json_object *payload, *on;
    if (!field_equals(message, "nodeId", "lightpi") ||
        !json_object_object_get_ex(message, "payload", &payload) || !json_object_is_type(payload, json_type_object)) {
        return EPROTO;
    }
    if (field_equals(message, "type", "LightStateQuery")) {
        const char *query_id = read_id(payload, "queryId");
        return query_id == NULL ? EPROTO : light_rte_on_state_query(rte, query_id);
    }
    const char *command_id = read_id(payload, "commandId");
    if (command_id == NULL) {
        return EPROTO;
    }
    if (field_equals(message, "type", "BlindCommand")) {
        return receive_blind(rte, payload, command_id);
    }
    if (!field_equals(message, "type", "LightCommand") ||
        !json_object_object_get_ex(payload, "on", &on) || !json_object_is_type(on, json_type_boolean)) {
        return EPROTO;
    }
    // The RTE consumes the parsed string before this frame's JSON storage is released.
    const struct light_command command = {.command_id = command_id, .on = json_object_get_boolean(on)};
    return light_rte_receive_command(rte, &command);
}

int light_protocol_handle(struct light_rte *rte, const char *text, size_t length)
{
    struct json_tokener *parser = json_tokener_new();
    if (parser == NULL) {
        return ENOMEM;
    }
    json_tokener_set_flags(parser, JSON_TOKENER_STRICT | JSON_TOKENER_VALIDATE_UTF8);
    struct json_object *message = json_tokener_parse_ex(parser, text, (int)length);
    bool valid = json_tokener_get_error(parser) == json_tokener_success &&
                 json_tokener_get_parse_end(parser) == length && json_object_is_type(message, json_type_object);
    json_tokener_free(parser);
    int error = valid ? receive_message(rte, message) : EPROTO;
    json_object_put(message);
    return error;
}
