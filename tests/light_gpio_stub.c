#define _POSIX_C_SOURCE 200809L

#include "light_gpio_output.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

#ifdef PILAB_TRACE_LIGHT_WRITES
#include "light_rte.h"

#include <inttypes.h>
#include <json-c/json.h>
#include <time.h>

static const char *active_command_id;

int __real_light_rte_receive_command(struct light_rte *rte, const struct light_command *command);

int __wrap_light_rte_receive_command(struct light_rte *rte, const struct light_command *command)
{
    // The LightPi receive task applies one command at a time.
    active_command_id = command->command_id;
    int result = __real_light_rte_receive_command(rte, command);
    active_command_id = NULL;
    return result;
}

static int log_gpio_write(unsigned int offset, bool on)
{
    struct timespec applied;
    if (clock_gettime(CLOCK_MONOTONIC, &applied) != 0) {
        return errno;
    }
    if (active_command_id == NULL) {
        return EPROTO;
    }
    struct json_object *id = json_object_new_string(active_command_id);
    if (id == NULL) {
        return ENOMEM;
    }
    const char *quoted = json_object_to_json_string_ext(id, JSON_C_TO_STRING_NOSLASHESCAPE);
    uint64_t timestamp_ns = (uint64_t)applied.tv_sec * UINT64_C(1000000000) + (uint64_t)applied.tv_nsec;
    int status = quoted != NULL &&
        printf("{\"event\":\"gpio\",\"offset\":%u,\"on\":%s,\"timestamp_ns\":%" PRIu64
               ",\"commandId\":%s}\n", offset, on ? "true" : "false", timestamp_ns, quoted) >= 0 ? 0 : EIO;
    json_object_put(id);
    return status;
}
#endif

static bool level;
static bool fail_write;

int __wrap_light_gpio_open(struct light_gpio_output *gpio, const char *chip,
                           unsigned int offset, bool initial_on)
{
    if (offset != 18) {
        return EINVAL;
    }
    gpio->offset = offset;
    level = initial_on;
    fail_write = strcmp(chip, "test:fail") == 0;
    return 0;
}

void __wrap_light_gpio_close(struct light_gpio_output *gpio)
{
    (void)gpio;
}

int __wrap_light_gpio_read(const struct light_gpio_output *gpio, bool *on)
{
    (void)gpio;
    *on = level;
    return 0;
}

int __wrap_light_gpio_set(void *context, bool on)
{
    struct light_gpio_output *gpio = context;
    if (fail_write) {
        return EIO;
    }
    level = on;
#ifdef PILAB_TRACE_LIGHT_WRITES
    return log_gpio_write(gpio->offset, level);
#else
    printf("{\"event\":\"gpio\",\"offset\":%u,\"on\":%s}\n",
           gpio->offset, level ? "true" : "false");
    return 0;
#endif
}
