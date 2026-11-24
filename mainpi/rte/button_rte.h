#ifndef MAINPI_BUTTON_RTE_H
#define MAINPI_BUTTON_RTE_H

#include <stdbool.h>

struct button_rte_ports {
    int (*send_button_pressed)(void *context);
    void *context;
};

/* The input task supplies a confirmed state and a connected output port.
 * The runnable and output callback execute synchronously on that task.
 * Returns the output callback's status for a press, or 0 for a release.
 */
int button_rte_receive_button_state_changed(const struct button_rte_ports *ports, bool pressed);

#endif
