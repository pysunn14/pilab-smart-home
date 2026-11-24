#include "button_debounce.h"

#include <assert.h>

static const uint64_t ms = 1000000;

static void bouncing_press_is_confirmed_once(void)
{
    struct button_debounce button;
    button_debounce_init(&button, true, 10 * ms);

    assert(button_debounce_on_edge(&button, false, 0) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, true, 2 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, false, 4 * ms) == BUTTON_UNCHANGED);

    assert(button_debounce_on_timer(&button, 13 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 14 * ms) == BUTTON_PRESSED);
    assert(button_debounce_on_timer(&button, 15 * ms) == BUTTON_UNCHANGED);
}

static void a_short_pulse_is_filtered(void)
{
    struct button_debounce button;
    button_debounce_init(&button, true, 10 * ms);

    assert(button_debounce_on_edge(&button, false, 0) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, true, 9 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 20 * ms) == BUTTON_UNCHANGED);
}

static void a_stable_release_allows_the_next_press(void)
{
    struct button_debounce button;
    button_debounce_init(&button, true, 10 * ms);

    assert(button_debounce_on_edge(&button, false, 0) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 10 * ms) == BUTTON_PRESSED);

    assert(button_debounce_on_edge(&button, true, 20 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, false, 22 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, true, 24 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 33 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 34 * ms) == BUTTON_RELEASED);

    assert(button_debounce_on_edge(&button, false, 40 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 50 * ms) == BUTTON_PRESSED);
}

static void a_late_timer_does_not_erase_a_stable_press(void)
{
    struct button_debounce button;
    button_debounce_init(&button, true, 10 * ms);

    assert(button_debounce_on_edge(&button, false, 0) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, true, 15 * ms) == BUTTON_PRESSED);
    assert(button_debounce_on_timer(&button, 25 * ms) == BUTTON_RELEASED);
}

static void a_button_held_at_startup_emits_no_press(void)
{
    struct button_debounce button;
    button_debounce_init(&button, false, 10 * ms);

    assert(button_debounce_on_timer(&button, 10 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_edge(&button, true, 20 * ms) == BUTTON_UNCHANGED);
    assert(button_debounce_on_timer(&button, 30 * ms) == BUTTON_RELEASED);
}

int main(void)
{
    bouncing_press_is_confirmed_once();
    a_short_pulse_is_filtered();
    a_stable_release_allows_the_next_press();
    a_late_timer_does_not_erase_a_stable_press();
    a_button_held_at_startup_emits_no_press();
    return 0;
}
