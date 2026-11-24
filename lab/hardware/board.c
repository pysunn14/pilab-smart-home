#include "pigpio.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/spi/spidev.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

int __real_open(const char *path, int flags, ...);
ssize_t __real_read(int fd, void *buffer, size_t size);
ssize_t __real_write(int fd, const void *buffer, size_t size);
int __real_ioctl(int fd, unsigned long request, ...);
ssize_t __real_send(int fd, const void *buffer, size_t size, int flags);

static _Thread_local int pwm_armed;
static _Thread_local int motor_direction[2];

static void trace(const char *event, unsigned pin, int value)
{
    struct timespec time;
    clock_gettime(CLOCK_MONOTONIC, &time);
    long long ns = (long long)time.tv_sec * 1000000000 + time.tv_nsec;
    printf("{\"event\":\"%s\",\"pin\":%u,\"value\":%d,\"timestamp_ns\":%lld}\n",
           event, pin, value, ns);
    fflush(stdout);
}

void lab_sensor(const char *name, int channel)
{
    trace(name, (unsigned)channel, 24);
}

void lab_arm_pwm(void)
{
    pwm_armed = 1;
}

void lab_tcp_connected(void)
{
    trace("tcp_connected", 0, 1);
}

void lab_fire_frame(const char *message, unsigned size)
{
    int recognized = strcmp(message, "FIRE_ON\n") == 0 ? 1 : strcmp(message, "NORMAL\n") == 0 ? 0 : -1;
    trace("fire_frame", size, recognized);
}

ssize_t __wrap_send(int fd, const void *buffer, size_t size, int flags)
{
    if (unlink("/board/cut-local-tcp") == 0) {
        if (shutdown(fd, SHUT_RDWR) == 0) {
            trace("tcp_shutdown", 0, 1);
        } else {
            trace("tcp_shutdown_error", 0, errno);
        }
    }
    return __real_send(fd, buffer, size, flags);
}

int __wrap_open(const char *path, int flags, ...)
{
    const char gpio[] = "/sys/class/gpio";
    char mapped[128];
    if (strncmp(path, gpio, sizeof(gpio) - 1) == 0) {
        snprintf(mapped, sizeof(mapped), "/board/gpio%s", path + sizeof(gpio) - 1);
        path = mapped;
    } else if (strcmp(path, "/dev/spidev0.0") == 0) {
        path = "/board/spi";
    }
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        int mode = va_arg(args, int);
        va_end(args);
        return __real_open(path, flags, mode);
    }
    return __real_open(path, flags);
}

static int is_gpio_value(int fd, const char *path)
{
    char fd_path[64];
    char target[128];
    snprintf(fd_path, sizeof(fd_path), "/proc/self/fd/%d", fd);
    ssize_t length = readlink(fd_path, target, sizeof(target) - 1);
    if (length < 0) {
        return 0;
    }
    target[length] = '\0';
    return strcmp(target, path) == 0;
}

ssize_t __wrap_read(int fd, void *buffer, size_t size)
{
    static _Thread_local int previous_button = -1;
    ssize_t result = __real_read(fd, buffer, size);
    if (result > 0 && is_gpio_value(fd, "/board/gpio/gpio20/value")) {
        int value = ((const char *)buffer)[0] - '0';
        if (value != previous_button) {
            previous_button = value;
            trace("gpio_read", 20, value);
        }
    }
    return result;
}

ssize_t __wrap_write(int fd, const void *buffer, size_t size)
{
    ssize_t result = __real_write(fd, buffer, size);
    if (result == 1 && size == 1) {
        int value = ((const char *)buffer)[0] - '0';
        if (is_gpio_value(fd, "/board/gpio/gpio18/value")) {
            trace("gpio", 18, value);
        } else if (is_gpio_value(fd, "/board/gpio/gpio13/value")) {
            motor_direction[0] = value;
        } else if (is_gpio_value(fd, "/board/gpio/gpio19/value")) {
            motor_direction[1] = value;
        } else if (pwm_armed && value == 1 && is_gpio_value(fd, "/board/gpio/gpio26/value")) {
            trace("pwm_start", 26, motor_direction[0] - motor_direction[1]);
            pwm_armed = 0;
        }
    }
    return result;
}

static int adc(unsigned channel, uint8_t *rx)
{
    static _Thread_local unsigned last[8];
    static _Thread_local uint8_t seen[8];
    char name[32];
    snprintf(name, sizeof(name), "/board/adc%u", channel);
    FILE *fixture = fopen(name, "r");
    if (fixture == NULL) {
        perror(name);
        return -1;
    }
    unsigned value;
    int read = fscanf(fixture, "%u", &value);
    fclose(fixture);
    if (read != 1 || value > 1023) {
        fprintf(stderr, "Invalid ADC fixture: %s\n", name);
        errno = EINVAL;
        return -1;
    }
    if (!seen[channel] || last[channel] != value) {
        seen[channel] = 1;
        last[channel] = value;
        trace("adc_read", channel, (int)value);
    }
    rx[0] = 0;
    rx[1] = (uint8_t)(value >> 8);
    rx[2] = (uint8_t)value;
    return 3;
}

int __wrap_ioctl(int fd, unsigned long request, ...)
{
    va_list args;
    va_start(args, request);
    void *argument = va_arg(args, void *);
    va_end(args);

    switch (request) {
    case SPI_IOC_WR_MODE:
    case SPI_IOC_WR_BITS_PER_WORD:
    case SPI_IOC_WR_MAX_SPEED_HZ:
    case SPI_IOC_RD_MAX_SPEED_HZ:
        return 0;
    case SPI_IOC_MESSAGE(1): {
        struct spi_ioc_transfer *transfer = argument;
        return adc(0, (uint8_t *)(uintptr_t)transfer->rx_buf);
    }
    default:
        return __real_ioctl(fd, request, argument);
    }
}

int gpioInitialise(void) { return 0; }
void gpioTerminate(void) {}
int gpioSetMode(unsigned gpio, unsigned mode) { (void)gpio; (void)mode; return 0; }
int spiOpen(unsigned channel, unsigned baud, unsigned flags)
{
    (void)channel;
    (void)baud;
    (void)flags;
    return 0;
}
int spiClose(unsigned handle) { (void)handle; return 0; }

int gpioWrite(unsigned gpio, unsigned value)
{
    trace("gpio", gpio, (int)value);
    return 0;
}

int gpioServo(unsigned gpio, unsigned pulse)
{
    trace("servo", gpio, (int)pulse);
    return 0;
}

uint32_t gpioDelay(uint32_t micros)
{
    usleep(micros);
    return micros;
}

int spiXfer(unsigned handle, char *tx, char *rx, unsigned count)
{
    (void)handle;
    if (count != 3) {
        errno = EINVAL;
        return -1;
    }
    const uint8_t control = (uint8_t)tx[1];
    const unsigned channel = control & 0x80 ? 0 : (control >> 4) & 7;
    return adc(channel, (uint8_t *)rx);
}
