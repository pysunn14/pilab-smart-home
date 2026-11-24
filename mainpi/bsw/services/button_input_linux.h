#ifndef MAINPI_BSW_BUTTON_INPUT_LINUX_H
#define MAINPI_BSW_BUTTON_INPUT_LINUX_H

#include "button_input_task.h"

struct button_input_linux {
    int edge_fd;
    int stop_fd;
    int (*read_edge)(void *context, bool *gpio_high, uint64_t *timestamp_ns);
    void *edge_context;
};

/* Can be assigned to button_input_io.wait. The caller owns both descriptors;
 * use -1 for stop_fd when this source has no separate stop signal. On POLLIN,
 * read_edge consumes one edge and returns its CLOCK_MONOTONIC timestamp.
 */
int button_input_linux_wait(void *context, const uint64_t *deadline_ns,
                            struct button_input_event *event);

#endif
