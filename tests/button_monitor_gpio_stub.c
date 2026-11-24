#define _POSIX_C_SOURCE 200809L

#include "button_gpio_input.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int edge_fd;
static bool initial_high;

int __wrap_button_gpio_open(struct button_gpio_input *input, const char *chip_path,
                            unsigned int offset)
{
    (void)input;
    (void)offset;
    const char *fd = getenv("PILAB_TEST_EDGE_FD");
    if (fd == NULL) {
        return EINVAL;
    }
    edge_fd = atoi(fd);
    initial_high = strcmp(chip_path, "pipe:low") != 0;
    return 0;
}

void __wrap_button_gpio_close(struct button_gpio_input *input)
{
    (void)input;
    close(edge_fd);
}

int __wrap_button_gpio_fd(const struct button_gpio_input *input)
{
    (void)input;
    return edge_fd;
}

int __wrap_button_gpio_read_level(const struct button_gpio_input *input, bool *gpio_high)
{
    (void)input;
    *gpio_high = initial_high;
    return 0;
}

int __wrap_button_gpio_read_edge(void *context, bool *gpio_high, uint64_t *timestamp_ns)
{
    (void)context;
    unsigned char packet[sizeof *timestamp_ns + 1];
    ssize_t size = read(edge_fd, packet, sizeof packet);
    if (size != (ssize_t)sizeof packet) {
        return size < 0 ? errno : EIO;
    }
    memcpy(timestamp_ns, packet, sizeof *timestamp_ns);
    *gpio_high = packet[sizeof *timestamp_ns] != 0;
    return 0;
}
