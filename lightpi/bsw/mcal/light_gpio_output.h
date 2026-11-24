#ifndef LIGHTPI_LIGHT_GPIO_OUTPUT_H
#define LIGHTPI_LIGHT_GPIO_OUTPUT_H

#include <stdbool.h>

struct gpiod_line_request;

struct light_gpio_output {
    struct gpiod_line_request *request;
    unsigned int offset;
};

int light_gpio_open(struct light_gpio_output *gpio, const char *chip_path, unsigned int offset, bool initial_on);
void light_gpio_close(struct light_gpio_output *gpio);
int light_gpio_read(const struct light_gpio_output *gpio, bool *on);
int light_gpio_set(void *context, bool on);

#endif
