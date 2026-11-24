#define _POSIX_C_SOURCE 200809L
#include "air_room.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/stat.h>
#include <sys/timerfd.h>
#include <unistd.h>

enum { period_seconds = 3, room_line_capacity = 128 };

struct air_room {
    const char *path;
    telemetry_encode_sample validate;
    int timer_fd;
    int stop_fd;
    int ready_fd;
    pthread_t thread;
    pthread_mutex_t lock;
    char values[room_line_capacity];
    size_t length;
    int error;
};

static int notify(int fd)
{
    int result;
    do {
        result = eventfd_write(fd, 1);
    } while (result < 0 && errno == EINTR);
    return result;
}

static ssize_t read_room(const char *path, char *line, size_t capacity)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) {
        return -1;
    }
    struct stat file;
    ssize_t count = -1;
    if (fstat(fd, &file) == 0) {
        if (!S_ISREG(file.st_mode)) {
            errno = EINVAL;
        } else {
            do {
                count = read(fd, line, capacity);
            } while (count < 0 && errno == EINTR);
        }
    }
    int error = errno;
    close(fd);
    errno = error;
    return count;
}

static bool collect(struct air_room *room, uint64_t tick)
{
    char line[room_line_capacity];
    ssize_t count = read_room(room->path, line, sizeof line);
    if (count < 0) {
        fprintf(stderr, "air-node: room read at tick %" PRIu64 ": %s\n", tick, strerror(errno));
        return true;
    }
    if (count < 2 || count >= (ssize_t)sizeof line
            || memchr(line, '\n', (size_t)count) != line + count - 1 || memchr(line, '\0', (size_t)count)) {
        fprintf(stderr, "air-node: invalid room snapshot at tick %" PRIu64 "\n", tick);
        return true;
    }
    line[count - 1] = '\0';
    char values[room_line_capacity];
    if (room->validate(line, values, sizeof values) != TELEMETRY_SAMPLE_READY) {
        fprintf(stderr, "air-node: invalid room values at tick %" PRIu64 "\n", tick);
        return true;
    }
    // Each wakeup represents a collection, but only the newest unpublished value is kept.
    pthread_mutex_lock(&room->lock);
    room->length = strlen(values) + 1;
    memcpy(room->values, values, room->length);
    int notified = notify(room->ready_fd);
    pthread_mutex_unlock(&room->lock);
    if (notified < 0) {
        return false;
    }
    printf("{\"event\":\"room_sampled\",\"tick\":%" PRIu64 "}\n", tick);
    fflush(stdout);
    return true;
}

static void *sample_room(void *context)
{
    struct air_room *room = context;
    struct pollfd descriptors[] = {
        {.fd = room->stop_fd, .events = POLLIN},
        {.fd = room->timer_fd, .events = POLLIN},
    };
    uint64_t tick = 0;
    int error;
    for (;;) {
        int ready = poll(descriptors, 2, -1);
        if (ready < 0 && errno == EINTR) {
            continue;
        }
        if (ready < 0) {
            error = errno;
            break;
        }
        if (descriptors[0].revents != 0) {
            return NULL;
        }
        uint64_t elapsed;
        ssize_t size = read(room->timer_fd, &elapsed, sizeof elapsed);
        if (size < 0 && errno == EINTR) {
            continue;
        }
        if (size != (ssize_t)sizeof elapsed) {
            error = size < 0 ? errno : EIO;
            break;
        }
        tick += elapsed;
        if (elapsed > 1) {
            fprintf(stderr, "air-node: missed %" PRIu64 " room ticks\n", elapsed - 1);
        }
        if (!collect(room, tick)) {
            error = errno;
            break;
        }
    }

    pthread_mutex_lock(&room->lock);
    room->error = error;
    (void)notify(room->ready_fd);
    pthread_mutex_unlock(&room->lock);
    return NULL;
}

struct air_room *air_room_start(const char *path, telemetry_encode_sample validate)
{
    struct air_room *room = calloc(1, sizeof *room);
    if (room == NULL) {
        return NULL;
    }
    room->path = path;
    room->validate = validate;
    room->timer_fd = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    room->stop_fd = eventfd(0, EFD_CLOEXEC);
    room->ready_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    int error = 0;
    if (room->timer_fd < 0 || room->stop_fd < 0 || room->ready_fd < 0) {
        error = errno;
        goto failed;
    }
    struct itimerspec timer = {
        .it_value.tv_sec = period_seconds,
        .it_interval.tv_sec = period_seconds,
    };
    if (timerfd_settime(room->timer_fd, 0, &timer, NULL) < 0) {
        error = errno;
        goto failed;
    }
    error = pthread_mutex_init(&room->lock, NULL);
    if (error != 0) {
        goto failed;
    }
    error = pthread_create(&room->thread, NULL, sample_room, room);
    if (error == 0) {
        return room;
    }
    pthread_mutex_destroy(&room->lock);

failed:
    close(room->ready_fd);
    close(room->stop_fd);
    close(room->timer_fd);
    free(room);
    errno = error;
    return NULL;
}

int air_room_ready_fd(const struct air_room *room)
{
    return room->ready_fd;
}

enum telemetry_sample_status air_room_take(void *context, char *values, size_t capacity)
{
    struct air_room *room = context;
    eventfd_t pending = 0;
    pthread_mutex_lock(&room->lock);
    int error = room->error;
    if (error == 0) {
        if (eventfd_read(room->ready_fd, &pending) < 0) {
            error = errno;
        } else if (room->length > capacity) {
            error = EMSGSIZE;
        } else {
            memcpy(values, room->values, room->length);
        }
    }
    pthread_mutex_unlock(&room->lock);
    if (pending > 1) {
        printf("{\"event\":\"room_samples_replaced\",\"count\":%" PRIu64 "}\n", (uint64_t)pending - 1);
        fflush(stdout);
    }
    if (error != 0) {
        fprintf(stderr, "air-node: take room sample: %s\n", strerror(error));
        return TELEMETRY_SAMPLE_ERROR;
    }
    return TELEMETRY_SAMPLE_READY;
}

void air_room_stop(struct air_room *room)
{
    (void)notify(room->stop_fd);
    pthread_join(room->thread, NULL);
    pthread_mutex_destroy(&room->lock);
    close(room->ready_fd);
    close(room->stop_fd);
    close(room->timer_fd);
    free(room);
}
