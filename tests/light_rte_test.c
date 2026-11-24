#include "light_rte.h"

#include <assert.h>
#include <errno.h>
#include <string.h>

struct fake_gpio {
    bool on;
    unsigned int writes;
    int write_error;
};

struct fake_transport {
    const struct fake_gpio *gpio;
    const char *expected_id;
    unsigned int replies;
    int send_error;
};

static int set_light(void *context, bool on)
{
    struct fake_gpio *gpio = context;
    gpio->writes++;
    if (gpio->write_error != 0) {
        return gpio->write_error;
    }
    gpio->on = on;
    return 0;
}

static int send_applied(void *context, const struct light_command *command)
{
    struct fake_transport *transport = context;
    assert(transport->gpio->on == command->on);
    assert(strcmp(transport->expected_id, command->command_id) == 0);
    transport->replies++;
    return transport->send_error;
}

static int read_light(void *context, bool *on)
{
    const struct fake_gpio *gpio = context;
    *on = gpio->on;
    return 0;
}

static int send_snapshot(void *context, const char *query_id, const struct light_state *state)
{
    const struct fake_transport *transport = context;
    assert(strcmp(query_id, "query/1") == 0);
    assert(strcmp(state->last_applied_id, transport->expected_id) == 0);
    assert(!state->on);
    return 0;
}

int main(void)
{
    struct fake_gpio gpio = {0};
    struct fake_transport transport = {.gpio = &gpio};
    struct light_rte rte = {
        .ports = {
            .device = {.set_light = set_light, .context = &gpio},
            .read_light = read_light,
            .send_applied = send_applied,
            .send_state_snapshot = send_snapshot,
            .send_context = &transport,
        },
    };
    char first_id[] = "mainpi/run/1";
    const struct light_command turn_on = {.command_id = first_id, .on = true};
    const struct light_command repeat_on = {.command_id = "mainpi/run/1", .on = true};
    const struct light_command conflict = {.command_id = "mainpi/run/1", .on = false};
    const struct light_command turn_off = {.command_id = "mainpi/run/2", .on = false};

    transport.expected_id = turn_on.command_id;
    assert(light_rte_receive_command(&rte, &turn_on) == 0);
    assert(gpio.on && gpio.writes == 1 && transport.replies == 1);

    first_id[sizeof first_id - 2] = 'x';
    transport.expected_id = repeat_on.command_id;
    assert(light_rte_receive_command(&rte, &repeat_on) == 0);
    assert(gpio.on && gpio.writes == 1 && transport.replies == 2);
    assert(light_rte_receive_command(&rte, &conflict) == EPROTO);
    assert(gpio.on && gpio.writes == 1 && transport.replies == 2);

    gpio.write_error = EIO;
    assert(light_rte_receive_command(&rte, &turn_off) == EIO);
    assert(gpio.on && gpio.writes == 2 && transport.replies == 2);
    assert(light_rte_receive_command(&rte, &repeat_on) == 0);
    assert(gpio.on && gpio.writes == 2 && transport.replies == 3);

    gpio.write_error = 0;
    transport.expected_id = turn_off.command_id;
    assert(light_rte_receive_command(&rte, &turn_off) == 0);
    assert(!gpio.on && gpio.writes == 3 && transport.replies == 4);

    const struct light_command turn_on_again = {.command_id = "mainpi/run/3", .on = true};
    transport.expected_id = turn_on_again.command_id;
    transport.send_error = EPIPE;
    assert(light_rte_receive_command(&rte, &turn_on_again) == EPIPE);
    assert(gpio.on && gpio.writes == 4 && transport.replies == 5);

    transport.send_error = 0;
    assert(light_rte_receive_command(&rte, &turn_on_again) == 0);
    assert(gpio.on && gpio.writes == 4 && transport.replies == 6);
    gpio.on = false;
    assert(light_rte_on_state_query(&rte, "query/1") == 0);
    assert(gpio.writes == 4 && transport.replies == 6);
    light_rte_destroy(&rte);
    return 0;
}
