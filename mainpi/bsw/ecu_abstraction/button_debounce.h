#ifndef MAINPI_BSW_BUTTON_DEBOUNCE_H
#define MAINPI_BSW_BUTTON_DEBOUNCE_H

#include <stdbool.h>
#include <stdint.h>

enum button_transition {
    BUTTON_UNCHANGED,
    BUTTON_PRESSED,
    BUTTON_RELEASED,
};

struct button_debounce {
    uint64_t debounce_ns;
    uint64_t candidate_since_ns;
    bool stable_high;
    bool candidate_high;
};

/* Seed the active-low button from its observed startup level. */
void button_debounce_init(struct button_debounce *button, bool initial_high,
                          uint64_t debounce_ns);

/* Returns false when there is no candidate to confirm. */
bool button_debounce_next_deadline(const struct button_debounce *button,
                                   uint64_t *deadline_ns);

/* Supply nondecreasing timestamps from one monotonic clock. Drain queued
 * edges through now_ns before checking the timer for that time. Either call
 * can report a transition after the candidate has held for debounce_ns.
 */
enum button_transition button_debounce_on_edge(struct button_debounce *button,
                                               bool gpio_high, uint64_t edge_ns);
enum button_transition button_debounce_on_timer(struct button_debounce *button,
                                                uint64_t now_ns);

#endif
