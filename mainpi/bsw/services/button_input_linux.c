#define _GNU_SOURCE

#include "button_input_linux.h"

#include <errno.h>
#include <poll.h>
#include <stddef.h>
#include <time.h>

static const uint64_t ns_per_second = UINT64_C(1000000000);

int button_input_linux_wait(void *context, const uint64_t *deadline_ns,
                            struct button_input_event *event)
{
    const struct button_input_linux *input = context;
    if (input->edge_fd < 0) {
        return EBADF;
    }

    enum { EDGE_FD, STOP_FD };
    struct pollfd fds[] = {
        [EDGE_FD] = {.fd = input->edge_fd, .events = POLLIN},
        [STOP_FD] = {.fd = input->stop_fd, .events = POLLIN},
    };

    for (;;) {
        struct timespec remaining;
        const struct timespec *timeout = NULL;
        uint64_t now_ns = 0;
        bool deadline_reached = false;
        if (deadline_ns != NULL) {
            struct timespec now;
            if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) {
                return errno;
            }
            now_ns = (uint64_t)now.tv_sec * ns_per_second + (uint64_t)now.tv_nsec;
            deadline_reached = now_ns >= *deadline_ns;
            uint64_t remaining_ns = deadline_reached ? 0 : *deadline_ns - now_ns;
            remaining = (struct timespec){
                .tv_sec = (time_t)(remaining_ns / ns_per_second),
                .tv_nsec = (long)(remaining_ns % ns_per_second),
            };
            timeout = &remaining;
        }

        int ready = ppoll(fds, sizeof fds / sizeof fds[0], timeout, NULL);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            return errno;
        }
        if (ready == 0) {
            /* After a timed wait, sample the clock and check ready edges once
             * more before publishing a timer event at the sampled time.
             */
            if (!deadline_reached) {
                continue;
            }
            *event = (struct button_input_event){
                .kind = BUTTON_INPUT_TIMER,
                .timestamp_ns = now_ns,
            };
            return 0;
        }

        if ((fds[EDGE_FD].revents | fds[STOP_FD].revents) & POLLNVAL) {
            return EBADF;
        }
        if (fds[STOP_FD].revents & POLLIN) {
            *event = (struct button_input_event){.kind = BUTTON_INPUT_STOP};
            return 0;
        }
        if (fds[STOP_FD].revents != 0 || !(fds[EDGE_FD].revents & POLLIN)) {
            return EIO;
        }

        *event = (struct button_input_event){.kind = BUTTON_INPUT_EDGE};
        return input->read_edge(input->edge_context, &event->gpio_high,
                                &event->timestamp_ns);
    }
}
