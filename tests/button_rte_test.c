#include "button_rte.h"

#include <assert.h>
#include <errno.h>

struct recorder {
    unsigned int presses;
    int send_error;
};

static int record_button_pressed(void *context)
{
    struct recorder *recorder = context;
    recorder->presses++;
    return recorder->send_error;
}

int main(void)
{
    struct recorder recorder = {0};
    const struct button_rte_ports ports = {
        .send_button_pressed = record_button_pressed,
        .context = &recorder,
    };

    assert(button_rte_receive_button_state_changed(&ports, true) == 0);
    assert(recorder.presses == 1);
    assert(button_rte_receive_button_state_changed(&ports, false) == 0);
    assert(recorder.presses == 1);
    assert(button_rte_receive_button_state_changed(&ports, true) == 0);
    assert(recorder.presses == 2);

    recorder.send_error = ENOSPC;
    assert(button_rte_receive_button_state_changed(&ports, false) == 0);
    assert(recorder.presses == 2);
    assert(button_rte_receive_button_state_changed(&ports, true) == ENOSPC);
    assert(recorder.presses == 3);
    return 0;
}
