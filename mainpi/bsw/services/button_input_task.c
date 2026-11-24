#include "button_input_task.h"

#include <errno.h>
#include <stddef.h>

int button_input_run(const struct button_input_io *io, void *context,
                     bool initial_high, uint64_t debounce_ns)
{
    struct button_debounce button;
    button_debounce_init(&button, initial_high, debounce_ns);

    for (;;) {
        uint64_t deadline_ns;
        bool pending = button_debounce_next_deadline(&button, &deadline_ns);
        struct button_input_event event;
        int error = io->wait(context, pending ? &deadline_ns : NULL, &event);
        if (error != 0) {
            return error;
        }

        /* A later edge may confirm the old candidate before replacing it. */
        uint64_t edge_ns = button.candidate_since_ns;
        enum button_transition transition;
        switch (event.kind) {
        case BUTTON_INPUT_EDGE:
            transition = button_debounce_on_edge(&button, event.gpio_high,
                                                 event.timestamp_ns);
            break;
        case BUTTON_INPUT_TIMER:
            transition = button_debounce_on_timer(&button, event.timestamp_ns);
            break;
        case BUTTON_INPUT_STOP:
            return 0;
        default:
            return EINVAL;
        }

        if (transition != BUTTON_UNCHANGED) {
            io->publish(context, transition, edge_ns);
        }
    }
}
