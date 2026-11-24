#include "button_debounce.h"

void button_debounce_init(struct button_debounce *button, bool initial_high,
                          uint64_t debounce_ns)
{
    *button = (struct button_debounce){
        .debounce_ns = debounce_ns,
        .stable_high = initial_high,
        .candidate_high = initial_high,
    };
}

bool button_debounce_next_deadline(const struct button_debounce *button,
                                   uint64_t *deadline_ns)
{
    if (button->candidate_high == button->stable_high) {
        return false;
    }

    *deadline_ns = button->candidate_since_ns + button->debounce_ns;
    return true;
}

enum button_transition button_debounce_on_edge(struct button_debounce *button,
                                               bool gpio_high, uint64_t edge_ns)
{
    /* Confirm an already-stable candidate before a later edge replaces it. */
    enum button_transition transition = button_debounce_on_timer(button, edge_ns);

    if (gpio_high != button->candidate_high) {
        button->candidate_high = gpio_high;
        button->candidate_since_ns = edge_ns;
    }
    return transition;
}

enum button_transition button_debounce_on_timer(struct button_debounce *button,
                                                uint64_t now_ns)
{
    if (button->candidate_high == button->stable_high ||
        now_ns - button->candidate_since_ns < button->debounce_ns) {
        return BUTTON_UNCHANGED;
    }

    button->stable_high = button->candidate_high;
    return button->stable_high ? BUTTON_RELEASED : BUTTON_PRESSED;
}
