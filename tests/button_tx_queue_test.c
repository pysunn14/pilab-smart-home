#include "button_tx_queue.h"

#include <assert.h>
#include <errno.h>
#include <sched.h>

static void preserves_pending_presses_when_full(void)
{
    uint64_t storage[2];
    struct button_tx_queue queue;
    assert(button_tx_queue_init(&queue, storage, 2) == 0);

    assert(button_tx_queue_push(&queue, 1) == 0);
    assert(button_tx_queue_push(&queue, 2) == 0);
    assert(button_tx_queue_push(&queue, 3) == ENOSPC);

    uint64_t sequence;
    assert(button_tx_queue_pop(&queue, &sequence) == 0 && sequence == 1);
    assert(button_tx_queue_push(&queue, 4) == 0);
    assert(button_tx_queue_pop(&queue, &sequence) == 0 && sequence == 2);
    assert(button_tx_queue_pop(&queue, &sequence) == 0 && sequence == 4);
    assert(button_tx_queue_pop(&queue, &sequence) == EAGAIN && sequence == 4);
    assert(button_tx_queue_destroy(&queue) == 0);
}

enum { CONCURRENT_PRESSES = 4096 };

static void *produce_presses(void *context)
{
    struct button_tx_queue *queue = context;
    for (uint64_t sequence = 1; sequence <= CONCURRENT_PRESSES; sequence++) {
        int error;
        while ((error = button_tx_queue_push(queue, sequence)) == ENOSPC) {
            sched_yield(); /* The test waits for space without dropping an input. */
        }
        assert(error == 0);
    }
    return NULL;
}

static void producer_and_consumer_preserve_order(void)
{
    uint64_t storage[4];
    struct button_tx_queue queue;
    assert(button_tx_queue_init(&queue, storage, 4) == 0);

    pthread_t producer;
    assert(pthread_create(&producer, NULL, produce_presses, &queue) == 0);
    for (uint64_t expected = 1; expected <= CONCURRENT_PRESSES; expected++) {
        uint64_t sequence;
        assert(button_tx_queue_wait_pop(&queue, &sequence) == 0 && sequence == expected);
    }
    assert(pthread_join(producer, NULL) == 0);
    uint64_t sequence;
    assert(button_tx_queue_pop(&queue, &sequence) == EAGAIN);
    assert(button_tx_queue_destroy(&queue) == 0);
}

static void close_preserves_queued_events_and_ends_the_wait(void)
{
    uint64_t storage[2];
    struct button_tx_queue queue;
    assert(button_tx_queue_init(&queue, storage, 2) == 0);
    assert(button_tx_queue_push(&queue, 1) == 0);
    assert(button_tx_queue_close(&queue) == 0);
    assert(button_tx_queue_close(&queue) == 0);
    assert(button_tx_queue_push(&queue, 2) == ECANCELED);

    uint64_t sequence;
    assert(button_tx_queue_wait_pop(&queue, &sequence) == 0 && sequence == 1);
    assert(button_tx_queue_wait_pop(&queue, &sequence) == ECANCELED);
    assert(button_tx_queue_destroy(&queue) == 0);
}

int main(void)
{
    preserves_pending_presses_when_full();
    producer_and_consumer_preserve_order();
    close_preserves_queued_events_and_ends_the_wait();
    return 0;
}
