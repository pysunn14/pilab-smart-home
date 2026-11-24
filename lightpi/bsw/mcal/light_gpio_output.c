#include "light_gpio_output.h"

#include <errno.h>
#include <gpiod.h>

static enum gpiod_line_value line_value(bool on)
{
    return on ? GPIOD_LINE_VALUE_ACTIVE : GPIOD_LINE_VALUE_INACTIVE;
}

static int request_output(struct light_gpio_output *gpio, struct gpiod_chip *chip,
                          unsigned int offset, bool initial_on)
{
    struct gpiod_line_settings *settings = gpiod_line_settings_new();
    if (settings == NULL) {
        return errno;
    }
    struct gpiod_line_config *config = gpiod_line_config_new();
    if (config == NULL) {
        int error = errno;
        gpiod_line_settings_free(settings);
        return error;
    }

    int error = 0;
    if (gpiod_line_settings_set_direction(settings, GPIOD_LINE_DIRECTION_OUTPUT) < 0 ||
        gpiod_line_settings_set_output_value(settings, line_value(initial_on)) < 0 ||
        gpiod_line_config_add_line_settings(config, &offset, 1, settings) < 0) {
        error = errno;
    } else {
        gpio->request = gpiod_chip_request_lines(chip, NULL, config);
        if (gpio->request == NULL) {
            error = errno;
        } else {
            gpio->offset = offset;
        }
    }
    gpiod_line_config_free(config);
    gpiod_line_settings_free(settings);
    return error;
}

int light_gpio_open(struct light_gpio_output *gpio, const char *chip_path, unsigned int offset, bool initial_on)
{
    struct gpiod_chip *chip = gpiod_chip_open(chip_path);
    if (chip == NULL) {
        return errno;
    }
    int error = request_output(gpio, chip, offset, initial_on);
    gpiod_chip_close(chip);
    return error;
}

void light_gpio_close(struct light_gpio_output *gpio)
{
    gpiod_line_request_release(gpio->request);
}

int light_gpio_read(const struct light_gpio_output *gpio, bool *on)
{
    enum gpiod_line_value value = gpiod_line_request_get_value(gpio->request, gpio->offset);
    if (value == GPIOD_LINE_VALUE_ERROR) {
        int error = errno;
        return error != 0 ? error : EIO;
    }
    *on = value == GPIOD_LINE_VALUE_ACTIVE;
    return 0;
}

int light_gpio_set(void *context, bool on)
{
    struct light_gpio_output *gpio = context;
    if (gpiod_line_request_set_value(gpio->request, gpio->offset, line_value(on)) < 0) {
        return errno;
    }
    bool observed_on;
    int error = light_gpio_read(gpio, &observed_on);
    if (error != 0) {
        return error;
    }
    return observed_on == on ? 0 : EIO;
}
