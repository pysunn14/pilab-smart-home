#include "button_input_task.h"

#include <assert.h>
#include <errno.h>
#include <stddef.h>

enum { MS = 1000000 };

struct wait_step {
    uint64_t deadline_ns; /* Zero expects an indefinite wait in this fixture. */
    struct button_input_event event;
    int error;
};

struct input_fixture {
    const struct wait_step *steps;
    size_t step_count;
    size_t next_step;
    size_t press_after_step;
    uint64_t press_edge_ns;
    unsigned presses;
};

static int scripted_wait(void *context, const uint64_t *deadline_ns,
                         struct button_input_event *event)
{
    struct input_fixture *fixture = context;
    assert(fixture->next_step < fixture->step_count);
    const struct wait_step *step = &fixture->steps[fixture->next_step++];

    if (step->deadline_ns != 0) {
        assert(deadline_ns != NULL);
        assert(*deadline_ns == step->deadline_ns);
    } else {
        assert(deadline_ns == NULL);
    }

    *event = step->event;
    return step->error;
}

static void record_transition(void *context, enum button_transition transition, uint64_t edge_ns)
{
    struct input_fixture *fixture = context;
    assert(transition == BUTTON_PRESSED);
    assert(fixture->next_step == fixture->press_after_step);
    assert(edge_ns == fixture->press_edge_ns);
    fixture->presses++;
}

static void run_scenario(const struct wait_step *steps, size_t count,
                         size_t press_after_step, uint64_t press_edge_ns, int expected_result)
{
    const struct button_input_io io = {
        .wait = scripted_wait,
        .publish = record_transition,
    };
    struct input_fixture fixture = {
        .steps = steps,
        .step_count = count,
        .press_after_step = press_after_step,
        .press_edge_ns = press_edge_ns,
    };

    assert(button_input_run(&io, &fixture, true, 10 * MS) == expected_result);
    assert(fixture.next_step == count);
    assert(fixture.presses == (press_after_step != 0));
}

static void held_press_is_confirmed_by_the_timer(void)
{
    const struct wait_step steps[] = {
        {.event = {.kind = BUTTON_INPUT_EDGE, .gpio_high = false}},
        {.deadline_ns = 10 * MS,
         .event = {.kind = BUTTON_INPUT_TIMER, .timestamp_ns = 10 * MS}},
        {.event = {.kind = BUTTON_INPUT_STOP}},
    };

    run_scenario(steps, sizeof steps / sizeof steps[0], 2, 0, 0);
}

static void short_pulse_cancels_the_confirmation_deadline(void)
{
    const struct wait_step steps[] = {
        {.event = {.kind = BUTTON_INPUT_EDGE, .gpio_high = false}},
        {.deadline_ns = 10 * MS,
         .event = {.kind = BUTTON_INPUT_EDGE, .timestamp_ns = 2 * MS, .gpio_high = true}},
        {.event = {.kind = BUTTON_INPUT_STOP}},
    };

    run_scenario(steps, sizeof steps / sizeof steps[0], 0, 0, 0);
}

static void read_error_is_returned_to_the_caller(void)
{
    const struct wait_step steps[] = {{.error = EIO}};

    run_scenario(steps, sizeof steps / sizeof steps[0], 0, 0, EIO);
}

static void another_edge_sets_a_new_deadline(void)
{
    const struct wait_step steps[] = {
        {.event = {.kind = BUTTON_INPUT_EDGE, .gpio_high = false}},
        {.deadline_ns = 10 * MS,
         .event = {.kind = BUTTON_INPUT_EDGE, .timestamp_ns = 2 * MS, .gpio_high = true}},
        {.event = {.kind = BUTTON_INPUT_EDGE, .timestamp_ns = 4 * MS, .gpio_high = false}},
        {.deadline_ns = 14 * MS,
         .event = {.kind = BUTTON_INPUT_TIMER, .timestamp_ns = 14 * MS}},
        {.event = {.kind = BUTTON_INPUT_STOP}},
    };

    run_scenario(steps, sizeof steps / sizeof steps[0], 4, 4 * MS, 0);
}

static void confirming_edge_can_arrive_before_the_timer_is_serviced(void)
{
    const struct wait_step steps[] = {
        {.event = {.kind = BUTTON_INPUT_EDGE, .timestamp_ns = 4 * MS, .gpio_high = false}},
        {.deadline_ns = 14 * MS,
         .event = {.kind = BUTTON_INPUT_EDGE, .timestamp_ns = 15 * MS, .gpio_high = true}},
        {.deadline_ns = 25 * MS,
         .event = {.kind = BUTTON_INPUT_STOP}},
    };

    run_scenario(steps, sizeof steps / sizeof steps[0], 2, 4 * MS, 0);
}

int main(void)
{
    held_press_is_confirmed_by_the_timer();
    short_pulse_cancels_the_confirmation_deadline();
    another_edge_sets_a_new_deadline();
    confirming_edge_can_arrive_before_the_timer_is_serviced();
    read_error_is_returned_to_the_caller();
    return 0;
}
