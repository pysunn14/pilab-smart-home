#ifndef PILAB_BSW_TELEMETRY_PUBLISHER_H
#define PILAB_BSW_TELEMETRY_PUBLISHER_H

#include "websocket_transport.h"
#include <stdint.h>

enum telemetry_sample_status { TELEMETRY_SAMPLE_READY, TELEMETRY_SAMPLE_END, TELEMETRY_SAMPLE_ERROR,
                               TELEMETRY_SAMPLE_WAITING };

typedef enum telemetry_sample_status (*telemetry_encode_sample)(const char *line, char *values, size_t capacity);

struct telemetry_stream {
    int fd;
    const char *kind;
    telemetry_encode_sample encode;
    char buffer[128];
    size_t buffered;
    bool eof;
    /* Optional source that provides complete encoded samples when fd is readable. */
    enum telemetry_sample_status (*take_ready)(void *context, char *values, size_t capacity);
    void *context;
    /* Optional command callback. It may reply on the publisher's own socket. */
    CURLcode (*on_text)(void *context, struct websocket_transport *transport, const char *text, size_t length);
    /* Optional state report, sent before samples on every new connection. */
    CURLcode (*on_connected)(void *context, struct websocket_transport *transport);
    void *receiver_context;
};

/* Validate the first input line before the caller opens a connection. */
enum telemetry_sample_status telemetry_read_first_sample(struct telemetry_stream *stream,
                                                         char *values, size_t capacity);

/* Consume a buffered line or try one read after poll has signaled the descriptor. */
enum telemetry_sample_status telemetry_read_available(struct telemetry_stream *stream,
                                                       char *values, size_t capacity, bool readable);

bool telemetry_reading_is_valid(double value);
bool telemetry_format_sample(char *message, size_t capacity, const char *node_id, const char *run_id,
                             uint64_t sequence, const char *values);

/* Called once per node process on the caller's open connection. node_id is
 * the node's fixed protocol ID. The caller prepares the first JSON values
 * unless the stream provides a take_ready callback for its first and later samples.
 * The stream retains incomplete lines while the publisher waits for input
 * and socket events. A NULL stream sends one sample. The caller closes
 * the connection. */
int telemetry_publish_samples(struct websocket_transport *transport, const char *url, const char *node_id,
                              char *values, size_t capacity, struct telemetry_stream *stream);

#endif
