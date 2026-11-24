#ifndef LIGHTPI_LIGHT_SWC_H
#define LIGHTPI_LIGHT_SWC_H

#include <stdbool.h>

struct light_swc_ports {
    int (*set_light)(void *context, bool on);
    void *context;
};

/* Runnable for an absolute on/off request. Returns the device write status. */
int light_swc_on_command(const struct light_swc_ports *ports, bool on);

#endif
