#ifndef MAINPI_BSW_BUTTON_GPIO_INPUT_H
#define MAINPI_BSW_BUTTON_GPIO_INPUT_H

#include <stdbool.h>
#include <stdint.h>

struct gpiod_line_request;
struct gpiod_edge_event_buffer;

struct button_gpio_input {
    struct gpiod_line_request *request;
    struct gpiod_edge_event_buffer *buffer;
    unsigned int offset;
    uint32_t last_seqno;
};

/* Open a zero-initialized input using a gpiochip path and line offset.
 * Returns zero or an errno value. Close it after the input task has stopped.
 */
int button_gpio_open(struct button_gpio_input *input, const char *chip_path,
                     unsigned int offset);
void button_gpio_close(struct button_gpio_input *input);

/* Use the borrowed descriptor with ppoll; the GPIO request owns it. */
int button_gpio_fd(const struct button_gpio_input *input);
int button_gpio_read_level(const struct button_gpio_input *input, bool *gpio_high);

/* Used as button_input_linux.read_edge by the sole reader of the request. */
int button_gpio_read_edge(void *context, bool *gpio_high, uint64_t *timestamp_ns);

#endif
