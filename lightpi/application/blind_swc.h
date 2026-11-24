#ifndef LIGHTPI_BLIND_SWC_H
#define LIGHTPI_BLIND_SWC_H

enum blind_direction { BLIND_DOWN = -1, BLIND_UP = 1 };

struct blind_swc_ports {
    int (*set_direction)(void *context, enum blind_direction direction);
    void *context;
};

int blind_swc_on_command(const struct blind_swc_ports *ports, enum blind_direction direction);

#endif
