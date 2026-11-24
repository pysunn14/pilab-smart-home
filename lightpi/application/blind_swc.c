#include "blind_swc.h"

#include <errno.h>
#include <stddef.h>

int blind_swc_on_command(const struct blind_swc_ports *ports, enum blind_direction direction)
{
    if (direction != BLIND_UP && direction != BLIND_DOWN) {
        return EINVAL;
    }
    if (ports->set_direction == NULL) {
        return ENODEV;
    }
    return ports->set_direction(ports->context, direction);
}
