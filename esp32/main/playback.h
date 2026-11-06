#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "driver/i2s_std.h"

#define PLAYBACK_QUEUE_BYTES 16384
#define PLAYBACK_BLOCK_BYTES 2048
#define PLAYBACK_WRITE_TIMEOUT_MS 20
#define PLAYBACK_BLOCK_TIMEOUT_US 250000LL
#define PLAYBACK_TOKEN_BYTES 32
#define PLAYBACK_EOS_TIMEOUT_US 10000000LL
#define PLAYBACK_CANCEL_TIMEOUT_US 2000000LL

typedef struct {
    uint32_t queued_bytes;
    uint32_t high_water_bytes;
    uint32_t backpressure;
    uint32_t starvation;
    uint32_t bytes_written;
    uint32_t write_errors;
} playback_stats_t;

bool playback_init(i2s_chan_handle_t channel);
esp_err_t playback_bind_i2s(i2s_chan_handle_t channel); // Before channel enable.
bool playback_begin(const uint8_t token[PLAYBACK_TOKEN_BYTES]);
bool playback_end(const uint8_t token[PLAYBACK_TOKEN_BYTES]);
bool playback_take_ack(uint8_t token[PLAYBACK_TOKEN_BYTES]);
bool playback_cancel(const uint8_t token[PLAYBACK_TOKEN_BYTES]);
bool playback_take_cancel_ack(uint8_t token[PLAYBACK_TOKEN_BYTES]);
void playback_deinit(void); // Only before consumer task starts / after it stops.
void playback_set_connected(bool connected);
int playback_enqueue(const void *pcm, size_t size); // 1 accepted, 0 full, -1 inactive.
bool playback_failed(void);
int playback_step(void); // One bounded consumer operation; never called by TCP.
void playback_task(void *argument);
void playback_snapshot(playback_stats_t *snapshot);
