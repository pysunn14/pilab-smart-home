#ifndef PILAB_PIGPIO_H
#define PILAB_PIGPIO_H

#include <stdint.h>

#define PI_INPUT 0
#define PI_OUTPUT 1
#define PI_LOW 0
#define PI_HIGH 1

int gpioInitialise(void);
void gpioTerminate(void);
int gpioSetMode(unsigned gpio, unsigned mode);
int gpioWrite(unsigned gpio, unsigned value);
int gpioServo(unsigned gpio, unsigned pulse);
uint32_t gpioDelay(uint32_t micros);
int spiOpen(unsigned channel, unsigned baud, unsigned flags);
int spiClose(unsigned handle);
int spiXfer(unsigned handle, char *tx, char *rx, unsigned count);
void lab_sensor(const char *name, int channel);
void lab_arm_pwm(void);
void lab_tcp_connected(void);
void lab_fire_frame(const char *message, unsigned size);

#endif
