#ifndef MAINPI_BUTTON_SWC_H
#define MAINPI_BUTTON_SWC_H

#include <stdbool.h>

enum button_swc_event {
    BUTTON_SWC_NO_EVENT,
    BUTTON_SWC_PRESSED,
};

/* Runnable invoked for a confirmed change of the logical button state. */
enum button_swc_event button_swc_on_state_changed(bool pressed);

#endif
