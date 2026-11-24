#include "button_rte.h"
#include "button_swc.h"

int button_rte_receive_button_state_changed(const struct button_rte_ports *ports, bool pressed)
{
    if (button_swc_on_state_changed(pressed) == BUTTON_SWC_PRESSED) {
        return ports->send_button_pressed(ports->context);
    }
    return 0;
}
