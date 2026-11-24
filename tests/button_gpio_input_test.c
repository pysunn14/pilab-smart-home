#include "button_gpio_input.h"

#include <assert.h>
#include <errno.h>
#include <gpiod.h>

/* Test stand-in for the opaque libgpiod event returned by the wrappers. */
struct gpiod_edge_event {
    enum gpiod_edge_event_type type;
    uint64_t timestamp_ns;
    unsigned long seqno;
};

static struct gpiod_edge_event queued_edge;
static int read_error;

int __wrap_gpiod_line_request_read_edge_events(struct gpiod_line_request *request,
                                             struct gpiod_edge_event_buffer *buffer,
                                             size_t max_events)
{
    (void)request;
    (void)buffer;
    assert(max_events == 1);
    if (read_error != 0) {
        errno = read_error;
        return -1;
    }
    return 1;
}

struct gpiod_edge_event *__wrap_gpiod_edge_event_buffer_get_event(
    struct gpiod_edge_event_buffer *buffer, unsigned long index)
{
    (void)buffer;
    assert(index == 0);
    return &queued_edge;
}

enum gpiod_edge_event_type __wrap_gpiod_edge_event_get_event_type(
    struct gpiod_edge_event *event)
{
    return event->type;
}

uint64_t __wrap_gpiod_edge_event_get_timestamp_ns(struct gpiod_edge_event *event)
{
    return event->timestamp_ns;
}

unsigned long __wrap_gpiod_edge_event_get_line_seqno(struct gpiod_edge_event *event)
{
    return event->seqno;
}

static void preserves_the_raw_levels_and_kernel_timestamps(void)
{
    struct button_gpio_input input = {0};
    bool gpio_high = true;
    uint64_t timestamp_ns;

    queued_edge = (struct gpiod_edge_event){GPIOD_EDGE_EVENT_FALLING_EDGE, 4000000, 1};
    assert(button_gpio_read_edge(&input, &gpio_high, &timestamp_ns) == 0);
    assert(!gpio_high && timestamp_ns == 4000000);

    queued_edge = (struct gpiod_edge_event){GPIOD_EDGE_EVENT_RISING_EDGE, 21000000, 2};
    assert(button_gpio_read_edge(&input, &gpio_high, &timestamp_ns) == 0);
    assert(gpio_high && timestamp_ns == 21000000);

    queued_edge.seqno = 4;
    assert(button_gpio_read_edge(&input, &gpio_high, &timestamp_ns) == EOVERFLOW);
}

static void propagates_a_failed_edge_read(void)
{
    struct button_gpio_input input = {0};
    bool gpio_high;
    uint64_t timestamp_ns;
    read_error = EIO;
    assert(button_gpio_read_edge(&input, &gpio_high, &timestamp_ns) == EIO);
    read_error = 0;
}

static void reports_a_missing_gpiochip(void)
{
    struct button_gpio_input input = {0};
    assert(button_gpio_open(&input, "/dev/pilab-test-missing-gpiochip", 20) == ENOENT);
    assert(input.request == NULL && input.buffer == NULL);
}

int main(void)
{
    preserves_the_raw_levels_and_kernel_timestamps();
    propagates_a_failed_edge_read();
    reports_a_missing_gpiochip();
    return 0;
}
