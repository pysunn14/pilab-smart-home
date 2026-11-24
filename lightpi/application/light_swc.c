#include "light_swc.h"

int light_swc_on_command(const struct light_swc_ports *ports, bool on)
{
    return ports->set_light(ports->context, on);
}
