#include "button_gpio_input.h"

#include <errno.h>
#include <gpiod.h>

static int request_edge_line(struct gpiod_chip *chip, unsigned int offset,
                             struct gpiod_line_request **request)
{
    struct gpiod_line_settings *settings = gpiod_line_settings_new();
    if (settings == NULL) {
        return errno;
    }
    struct gpiod_line_config *lines = gpiod_line_config_new();
    if (lines == NULL) {
        int error = errno;
        gpiod_line_settings_free(settings);
        return error;
    }

    /* Send physical levels to ECU Abstraction, which owns button polarity
     * and debounce. The wait service uses this same monotonic clock.
     */
    gpiod_line_settings_set_active_low(settings, false);
    gpiod_line_settings_set_debounce_period_us(settings, 0);

    int error = 0;
    if (gpiod_line_settings_set_direction(settings, GPIOD_LINE_DIRECTION_INPUT) < 0 ||
        gpiod_line_settings_set_edge_detection(settings, GPIOD_LINE_EDGE_BOTH) < 0 ||
        gpiod_line_settings_set_event_clock(settings, GPIOD_LINE_CLOCK_MONOTONIC) < 0 ||
        gpiod_line_config_add_line_settings(lines, &offset, 1, settings) < 0) {
        error = errno;
    } else {
        *request = gpiod_chip_request_lines(chip, NULL, lines);
        if (*request == NULL) {
            error = errno;
        }
    }

    gpiod_line_config_free(lines);
    gpiod_line_settings_free(settings);
    return error;
}

int button_gpio_open(struct button_gpio_input *input, const char *chip_path,
                     unsigned int offset)
{
    if (input->request != NULL) {
        return EBUSY;
    }
    struct gpiod_chip *chip = gpiod_chip_open(chip_path);
    if (chip == NULL) {
        return errno;
    }

    struct gpiod_line_request *request = NULL;
    int error = request_edge_line(chip, offset, &request);
    gpiod_chip_close(chip);
    if (error != 0) {
        return error;
    }

    struct gpiod_edge_event_buffer *buffer = gpiod_edge_event_buffer_new(1);
    if (buffer == NULL) {
        error = errno;
        gpiod_line_request_release(request);
        return error;
    }
    *input = (struct button_gpio_input){.request = request, .buffer = buffer, .offset = offset};
    return 0;
}

void button_gpio_close(struct button_gpio_input *input)
{
    if (input->buffer != NULL) {
        gpiod_edge_event_buffer_free(input->buffer);
    }
    if (input->request != NULL) {
        gpiod_line_request_release(input->request);
    }
    *input = (struct button_gpio_input){0};
}

int button_gpio_fd(const struct button_gpio_input *input)
{
    return gpiod_line_request_get_fd(input->request);
}

int button_gpio_read_level(const struct button_gpio_input *input, bool *gpio_high)
{
    enum gpiod_line_value value = gpiod_line_request_get_value(input->request, input->offset);
    if (value == GPIOD_LINE_VALUE_ERROR) {
        return errno;
    }
    *gpio_high = value == GPIOD_LINE_VALUE_ACTIVE;
    return 0;
}

int button_gpio_read_edge(void *context, bool *gpio_high, uint64_t *timestamp_ns)
{
    struct button_gpio_input *input = context;
    int count = gpiod_line_request_read_edge_events(input->request, input->buffer, 1);
    if (count != 1) {
        return count < 0 ? errno : EIO;
    }

    struct gpiod_edge_event *edge = gpiod_edge_event_buffer_get_event(input->buffer, 0);
    if (edge == NULL) {
        return errno;
    }
    uint32_t seqno = (uint32_t)gpiod_edge_event_get_line_seqno(edge);
    if (seqno != (uint32_t)(input->last_seqno + 1U)) {
        return EOVERFLOW;
    }
    enum gpiod_edge_event_type type = gpiod_edge_event_get_event_type(edge);
    if (type != GPIOD_EDGE_EVENT_RISING_EDGE && type != GPIOD_EDGE_EVENT_FALLING_EDGE) {
        return EPROTO;
    }

    input->last_seqno = seqno;
    *gpio_high = type == GPIOD_EDGE_EVENT_RISING_EDGE;
    *timestamp_ns = gpiod_edge_event_get_timestamp_ns(edge);
    return 0;
}
