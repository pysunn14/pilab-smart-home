#include "button_swc.h"

enum button_swc_event button_swc_on_state_changed(bool pressed)
{
    return pressed ? BUTTON_SWC_PRESSED : BUTTON_SWC_NO_EVENT;
}
