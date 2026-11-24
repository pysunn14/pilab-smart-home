#define _POSIX_C_SOURCE 200809L

#include "button_input_linux.h"

#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <time.h>
#include <unistd.h>

#define NS_PER_MS UINT64_C(1000000)

static uint64_t monotonic_ns(void)
{
    struct timespec now;
    assert(clock_gettime(CLOCK_MONOTONIC, &now) == 0);
    return (uint64_t)now.tv_sec * 1000 * NS_PER_MS + (uint64_t)now.tv_nsec;
}

static int read_pipe_edge(void *context, bool *gpio_high, uint64_t *timestamp_ns)
{
    int fd = *(const int *)context;
    ssize_t size = read(fd, timestamp_ns, sizeof *timestamp_ns);
    if (size != (ssize_t)sizeof *timestamp_ns) {
        return size < 0 ? errno : EIO;
    }
    *gpio_high = false;
    return 0;
}

static struct button_input_linux pipe_input(int *edge_fd)
{
    return (struct button_input_linux){
        .edge_fd = *edge_fd,
        .stop_fd = -1,
        .read_edge = read_pipe_edge,
        .edge_context = edge_fd,
    };
}

static void *write_edge_after_a_pause(void *context)
{
    const int fd = *(const int *)context;
    const struct timespec pause = {.tv_nsec = 5 * NS_PER_MS};
    assert(nanosleep(&pause, NULL) == 0);
    uint64_t timestamp_ns = monotonic_ns();
    assert(write(fd, &timestamp_ns, sizeof timestamp_ns) == sizeof timestamp_ns);
    return NULL;
}

static void new_input_wakes_the_waiter(void)
{
    int pipe_fds[2];
    assert(pipe(pipe_fds) == 0);
    struct button_input_linux input = pipe_input(&pipe_fds[0]);
    pthread_t writer;
    assert(pthread_create(&writer, NULL, write_edge_after_a_pause, &pipe_fds[1]) == 0);

    const uint64_t deadline_ns = monotonic_ns() + 2000 * NS_PER_MS;
    struct button_input_event event;
    assert(button_input_linux_wait(&input, &deadline_ns, &event) == 0);
    assert(event.kind == BUTTON_INPUT_EDGE && !event.gpio_high);
    assert(event.timestamp_ns > 0 && event.timestamp_ns < deadline_ns);

    assert(pthread_join(writer, NULL) == 0);
    close(pipe_fds[0]);
    close(pipe_fds[1]);
}

static void empty_pipe_waits_until_the_deadline(void)
{
    int pipe_fds[2];
    assert(pipe(pipe_fds) == 0);
    struct button_input_linux input = pipe_input(&pipe_fds[0]);

    const uint64_t deadline_ns = monotonic_ns() + 10 * NS_PER_MS;
    struct button_input_event event;
    assert(button_input_linux_wait(&input, &deadline_ns, &event) == 0);
    assert(event.kind == BUTTON_INPUT_TIMER);
    assert(event.timestamp_ns >= deadline_ns && event.timestamp_ns <= monotonic_ns());

    close(pipe_fds[0]);
    close(pipe_fds[1]);
}

static void buffered_input_goes_before_an_expired_deadline(void)
{
    int pipe_fds[2];
    assert(pipe(pipe_fds) == 0);
    struct button_input_linux input = pipe_input(&pipe_fds[0]);
    const uint64_t edge_ns = monotonic_ns();
    assert(write(pipe_fds[1], &edge_ns, sizeof edge_ns) == sizeof edge_ns);

    const uint64_t deadline_ns = monotonic_ns();
    struct button_input_event event;
    assert(button_input_linux_wait(&input, &deadline_ns, &event) == 0);
    assert(event.kind == BUTTON_INPUT_EDGE && event.timestamp_ns == edge_ns);

    close(pipe_fds[0]);
    close(pipe_fds[1]);
}

static void stop_signal_ends_an_indefinite_wait(void)
{
    int edge_fds[2], stop_fds[2];
    assert(pipe(edge_fds) == 0);
    assert(pipe(stop_fds) == 0);
    struct button_input_linux input = pipe_input(&edge_fds[0]);
    input.stop_fd = stop_fds[0];
    assert(write(stop_fds[1], "x", 1) == 1);

    struct button_input_event event;
    assert(button_input_linux_wait(&input, NULL, &event) == 0);
    assert(event.kind == BUTTON_INPUT_STOP);

    close(edge_fds[0]);
    close(edge_fds[1]);
    close(stop_fds[0]);
    close(stop_fds[1]);
}

static void closed_input_is_reported_as_a_failure(void)
{
    int pipe_fds[2];
    assert(pipe(pipe_fds) == 0);
    struct button_input_linux input = pipe_input(&pipe_fds[0]);
    close(pipe_fds[1]);

    struct button_input_event event;
    assert(button_input_linux_wait(&input, NULL, &event) == EIO);

    close(pipe_fds[0]);
}

int main(void)
{
    new_input_wakes_the_waiter();
    empty_pipe_waits_until_the_deadline();
    buffered_input_goes_before_an_expired_deadline();
    stop_signal_ends_an_indefinite_wait();
    closed_input_is_reported_as_a_failure();
    return 0;
}
