#ifndef MAINPI_BSW_BUTTON_TX_QUEUE_H
#define MAINPI_BSW_BUTTON_TX_QUEUE_H

#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

struct button_tx_queue {
    pthread_mutex_t mutex;
    pthread_cond_t readable;
    uint64_t *slots;
    size_t capacity;
    size_t head;
    size_t count;
    bool closed;
};

/* The caller provides storage for capacity presses and keeps it alive until
 * destroy. Stop all queue users before destroying it.
 */
int button_tx_queue_init(struct button_tx_queue *queue, uint64_t *storage, size_t capacity);
int button_tx_queue_destroy(struct button_tx_queue *queue);

/* Close rejects new input, wakes readers, and leaves queued items available. */
int button_tx_queue_close(struct button_tx_queue *queue);

/* Zero on success. Push returns ENOSPC when full or ECANCELED after close.
 * Pop returns EAGAIN if open and empty, or ECANCELED if closed and drained.
 * Neither waits for space or data.
 */
int button_tx_queue_push(struct button_tx_queue *queue, uint64_t sequence);
int button_tx_queue_pop(struct button_tx_queue *queue, uint64_t *sequence);

/* Sleeps while open and empty. Both pop functions write sequence on success. */
int button_tx_queue_wait_pop(struct button_tx_queue *queue, uint64_t *sequence);

#endif
