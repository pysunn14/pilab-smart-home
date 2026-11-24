#ifndef MAINPI_BSW_BUTTON_INPUT_TASK_H
#define MAINPI_BSW_BUTTON_INPUT_TASK_H

#include "button_debounce.h"

enum button_input_event_kind {
    BUTTON_INPUT_EDGE,
    BUTTON_INPUT_TIMER,
    BUTTON_INPUT_STOP,
};

struct button_input_event {
    enum button_input_event_kind kind;
    uint64_t timestamp_ns;
    bool gpio_high;
};

struct button_input_io {
    /* Wait for an edge, stop, or the absolute deadline. A null deadline
     * disables the timer. Use one monotonic clock and deliver buffered edges
     * through the timer event's timestamp before reporting timer expiry.
     * A nonzero return reports an input failure.
     */
    int (*wait)(void *context, const uint64_t *deadline_ns,
                struct button_input_event *event);
    /* Time of the edge whose state was confirmed by the debounce interval. */
    void (*publish)(void *context, enum button_transition transition, uint64_t edge_ns);
};

/* Owns the debounce state for one input task. Returns zero on stop,
 * propagates wait errors, and rejects an invalid event kind with EINVAL.
 */
int button_input_run(const struct button_input_io *io, void *context,
                     bool initial_high, uint64_t debounce_ns);

#endif
