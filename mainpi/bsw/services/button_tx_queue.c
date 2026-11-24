#include "button_tx_queue.h"

#include <errno.h>

int button_tx_queue_init(struct button_tx_queue *queue, uint64_t *storage, size_t capacity)
{
    if (storage == NULL || capacity == 0) {
        return EINVAL;
    }
    queue->slots = storage;
    queue->capacity = capacity;
    queue->head = 0;
    queue->count = 0;
    queue->closed = false;
    int error = pthread_mutex_init(&queue->mutex, NULL);
    if (error != 0) {
        return error;
    }
    error = pthread_cond_init(&queue->readable, NULL);
    if (error != 0) {
        pthread_mutex_destroy(&queue->mutex);
    }
    return error;
}

int button_tx_queue_destroy(struct button_tx_queue *queue)
{
    int error = pthread_cond_destroy(&queue->readable);
    return error != 0 ? error : pthread_mutex_destroy(&queue->mutex);
}

static int unlock_with_result(struct button_tx_queue *queue, int result)
{
    int error = pthread_mutex_unlock(&queue->mutex);
    return error != 0 ? error : result;
}

int button_tx_queue_close(struct button_tx_queue *queue)
{
    int error = pthread_mutex_lock(&queue->mutex);
    if (error != 0) {
        return error;
    }
    queue->closed = true;
    int result = pthread_cond_broadcast(&queue->readable);
    return unlock_with_result(queue, result);
}

int button_tx_queue_push(struct button_tx_queue *queue, uint64_t sequence)
{
    int error = pthread_mutex_lock(&queue->mutex);
    if (error != 0) {
        return error;
    }

    if (queue->closed) {
        return unlock_with_result(queue, ECANCELED);
    }
    if (queue->count == queue->capacity) {
        return unlock_with_result(queue, ENOSPC);
    }
    size_t tail = (queue->head + queue->count) % queue->capacity;
    queue->slots[tail] = sequence;
    queue->count++;
    int result = pthread_cond_signal(&queue->readable);
    return unlock_with_result(queue, result);
}

static int pop_locked(struct button_tx_queue *queue, uint64_t *sequence)
{
    if (queue->count == 0) {
        return queue->closed ? ECANCELED : EAGAIN;
    }
    *sequence = queue->slots[queue->head];
    queue->head = (queue->head + 1) % queue->capacity;
    queue->count--;
    return 0;
}

int button_tx_queue_pop(struct button_tx_queue *queue, uint64_t *sequence)
{
    int error = pthread_mutex_lock(&queue->mutex);
    if (error != 0) {
        return error;
    }
    int result = pop_locked(queue, sequence);
    return unlock_with_result(queue, result);
}

int button_tx_queue_wait_pop(struct button_tx_queue *queue, uint64_t *sequence)
{
    int error = pthread_mutex_lock(&queue->mutex);
    if (error != 0) {
        return error;
    }
    while (queue->count == 0 && !queue->closed) {
        error = pthread_cond_wait(&queue->readable, &queue->mutex);
        if (error != 0) {
            return unlock_with_result(queue, error);
        }
    }
    int result = pop_locked(queue, sequence);
    return unlock_with_result(queue, result);
}
