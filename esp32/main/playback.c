#include "playback.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/ringbuf.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_attr.h"

static RingbufHandle_t playback_ring;
static SemaphoreHandle_t playback_mutex;
static i2s_chan_handle_t playback_channel;
static bool playback_connected;
static bool playback_error;
static bool playback_active;
static uint32_t playback_generation = 1;
static playback_stats_t playback_stats;
static uint8_t playback_scratch[PLAYBACK_BLOCK_BYTES]; // Single consumer only.
static portMUX_TYPE dma_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t dma_eof_count;
static size_t dma_event_bytes;
static uint32_t dma_capacity;
static bool playback_busy;
static bool playback_framed;
static bool playback_eos;
static bool playback_ack_ready;
static uint8_t playback_token[PLAYBACK_TOKEN_BYTES];
static int64_t playback_eos_deadline;
static bool playback_draining;
static uint32_t playback_drain_start;
static uint32_t playback_drain_events;
static bool playback_token_valid;
static bool playback_cancel_pending;
static bool playback_cancel_draining;
static bool playback_cancel_ack_ready;
static bool playback_cancel_token_valid;
static uint8_t playback_cancel_token[PLAYBACK_TOKEN_BYTES];
static bool playback_reset_pending;
static bool playback_channel_enabled = true; // Channel is enabled before init.
static uint32_t playback_cancel_start;
static uint32_t playback_cancel_events;
static int64_t playback_cancel_deadline;
static bool playback_stop_failed;
static bool playback_requires_start;

static bool IRAM_ATTR playback_on_sent(i2s_chan_handle_t channel, i2s_event_data_t *event, void *context) {
    (void)channel;
    (void)context;
    portENTER_CRITICAL_ISR(&dma_mux);
    ++dma_eof_count;
    dma_event_bytes = event->size;
    portEXIT_CRITICAL_ISR(&dma_mux);
    return false;
}

esp_err_t playback_bind_i2s(i2s_chan_handle_t channel) {
    i2s_chan_info_t info;
    esp_err_t result = i2s_channel_get_info(channel, &info);
    if (result != ESP_OK) return result;
    if (info.total_dma_buf_size == 0) return ESP_ERR_INVALID_STATE;
    dma_capacity = info.total_dma_buf_size;
    i2s_event_callbacks_t callbacks = {.on_sent = playback_on_sent};
    return i2s_channel_register_event_callback(channel, &callbacks, NULL);
}

bool playback_begin(const uint8_t token[PLAYBACK_TOKEN_BYTES]) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool valid = playback_connected && !playback_error && !playback_framed && !playback_cancel_pending &&
                 !playback_cancel_ack_ready &&
                 !playback_busy && playback_stats.queued_bytes == 0;
    if (valid && playback_requires_start) {
        valid = memcmp(token, playback_token, 16) == 0 &&
                memcmp(token + 16, playback_token + 16, 16) > 0;
    }
    if (valid) {
        memcpy(playback_token, token, PLAYBACK_TOKEN_BYTES);
        playback_framed = true;
        playback_token_valid = true;
        playback_requires_start = true;
        playback_cancel_token_valid = false;
        playback_eos = playback_ack_ready = playback_draining = false;
    }
    xSemaphoreGive(playback_mutex);
    return valid;
}

bool playback_end(const uint8_t token[PLAYBACK_TOKEN_BYTES]) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool valid = playback_connected && !playback_error && playback_framed && !playback_eos &&
                 !playback_ack_ready && memcmp(playback_token, token, PLAYBACK_TOKEN_BYTES) == 0;
    if (valid) {
        playback_eos = true;
        playback_eos_deadline = esp_timer_get_time() + PLAYBACK_EOS_TIMEOUT_US;
    }
    xSemaphoreGive(playback_mutex);
    return valid;
}

bool playback_take_ack(uint8_t token[PLAYBACK_TOKEN_BYTES]) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool ready = playback_connected && !playback_error && playback_ack_ready && !playback_cancel_pending;
    if (ready) {
        memcpy(token, playback_token, PLAYBACK_TOKEN_BYTES);
        playback_ack_ready = playback_framed = false;
    }
    xSemaphoreGive(playback_mutex);
    return ready;
}

bool playback_cancel(const uint8_t token[PLAYBACK_TOKEN_BYTES]) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool valid = playback_connected && !playback_error &&
        ((playback_token_valid && memcmp(playback_token, token, PLAYBACK_TOKEN_BYTES) == 0) ||
         (!playback_token_valid && playback_cancel_token_valid &&
          memcmp(playback_cancel_token, token, PLAYBACK_TOKEN_BYTES) == 0));
    if (valid && !playback_cancel_pending) {
        bool already_silent = !playback_token_valid && playback_cancel_token_valid;
        memcpy(playback_cancel_token, token, PLAYBACK_TOKEN_BYTES);
        playback_cancel_token_valid = true;
        playback_cancel_pending = true;
        playback_eos = playback_ack_ready = playback_draining = playback_framed = false;
        if (!already_silent) {
            if (++playback_generation == 0) ++playback_generation;
            size_t size;
            void *item;
            while ((item = xRingbufferReceive(playback_ring, &size, 0)) != NULL) {
                vRingbufferReturnItem(playback_ring, item);
            }
            playback_stats.queued_bytes = 0;
            playback_active = false;
            playback_reset_pending = true;
            playback_cancel_draining = playback_cancel_ack_ready = false;
        } else {
            playback_cancel_ack_ready = true; // Idempotent duplicate, no new reset.
        }
    }
    xSemaphoreGive(playback_mutex);
    return valid;
}

bool playback_take_cancel_ack(uint8_t token[PLAYBACK_TOKEN_BYTES]) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool ready = playback_connected && !playback_error && playback_cancel_ack_ready;
    if (ready) {
        memcpy(token, playback_cancel_token, PLAYBACK_TOKEN_BYTES);
        playback_cancel_ack_ready = playback_cancel_pending = playback_cancel_draining = false;
        playback_token_valid = false;
    }
    xSemaphoreGive(playback_mutex);
    return ready;
}

// Called by the single consumer only after the queue and its last write are
// finished. Two whole DMA rings plus two EOFs are a conservative zero-output
// barrier. auto_clear_after_cb prevents old descriptors replaying PCM.
static void playback_drain_locked(void) {
    if (!playback_eos || playback_error) return;
    if (esp_timer_get_time() >= playback_eos_deadline) {
        playback_error = true;
        ++playback_stats.write_errors;
        return;
    }
    portENTER_CRITICAL(&dma_mux);
    uint32_t count = dma_eof_count;
    size_t size = dma_event_bytes;
    portEXIT_CRITICAL(&dma_mux);
    if (!playback_draining) {
        if (size == 0 || dma_capacity == 0) return;
        playback_drain_start = count;
        playback_drain_events = 2 * ((dma_capacity + size - 1) / size) + 2;
        playback_draining = true;
    } else if ((uint32_t)(count - playback_drain_start) >= playback_drain_events) {
        playback_ack_ready = true;
        playback_eos = false;
    }
}

bool playback_init(i2s_chan_handle_t channel) {
    playback_mutex = xSemaphoreCreateMutex();
    if (playback_mutex == NULL) return false;
    playback_ring = xRingbufferCreate(PLAYBACK_QUEUE_BYTES, RINGBUF_TYPE_BYTEBUF);
    if (playback_ring == NULL) {
        vSemaphoreDelete(playback_mutex);
        playback_mutex = NULL;
        return false;
    }
    playback_channel = channel;
    playback_channel_enabled = true;
    playback_stop_failed = false;
    playback_reset_pending = false;
    playback_cancel_pending = playback_cancel_draining = playback_cancel_ack_ready = false;
    playback_token_valid = playback_cancel_token_valid = false;
    playback_requires_start = false;
    return true;
}

void playback_deinit(void) {
    if (playback_ring != NULL) vRingbufferDelete(playback_ring);
    if (playback_mutex != NULL) vSemaphoreDelete(playback_mutex);
    playback_ring = NULL;
    playback_mutex = NULL;
}

void playback_set_connected(bool connected) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    playback_connected = false;
    if (++playback_generation == 0) ++playback_generation;
    size_t size;
    void *item;
    while ((item = xRingbufferReceive(playback_ring, &size, 0)) != NULL) {
        vRingbufferReturnItem(playback_ring, item);
    }
    playback_stats.queued_bytes = 0;
    playback_active = false;
    playback_error = playback_stop_failed;
    playback_framed = playback_eos = playback_ack_ready = playback_draining = false;
    playback_cancel_pending = playback_cancel_draining = playback_cancel_ack_ready = false;
    playback_token_valid = playback_cancel_token_valid = false;
    playback_requires_start = false;
    if (!connected) playback_reset_pending = !playback_stop_failed;
    playback_connected = connected;
    xSemaphoreGive(playback_mutex);
}

int playback_enqueue(const void *pcm, size_t size) {
    if (size == 0 || size > PLAYBACK_BLOCK_BYTES || (size & 1)) return -1;
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    int result = -1;
    if (playback_connected && !playback_error && !playback_eos && !playback_ack_ready && !playback_cancel_pending &&
        (!playback_requires_start || playback_framed)) {
        if (xRingbufferSend(playback_ring, pcm, size, 0) == pdTRUE) {
            playback_stats.queued_bytes += size;
            if (playback_stats.queued_bytes > playback_stats.high_water_bytes) {
                playback_stats.high_water_bytes = playback_stats.queued_bytes;
            }
            result = 1;
        } else {
            ++playback_stats.backpressure;
            result = 0; // Retain the fragment at the TCP side; do not drop PCM.
        }
    }
    xSemaphoreGive(playback_mutex);
    return result;
}

bool playback_failed(void) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool failed = playback_error;
    xSemaphoreGive(playback_mutex);
    return failed;
}

void playback_snapshot(playback_stats_t *snapshot) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    *snapshot = playback_stats;
    xSemaphoreGive(playback_mutex);
}

static bool playback_current(uint32_t generation) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    bool current = playback_connected && playback_generation == generation && !playback_error &&
                   !playback_cancel_pending && !playback_reset_pending;
    xSemaphoreGive(playback_mutex);
    return current;
}

// Only the consumer touches channel enable/disable/preload, after its prior
// write has returned. SDK disable resets DMA cursors; enable resets TX FIFO.
static int playback_control_step(void) {
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    if (playback_stop_failed) {
        xSemaphoreGive(playback_mutex);
        return -1;
    }
    bool reset = playback_reset_pending;
    uint32_t generation = playback_generation;
    xSemaphoreGive(playback_mutex);
    if (reset) {
        esp_err_t result = ESP_OK;
        if (playback_channel_enabled) {
            result = i2s_channel_disable(playback_channel);
            if (result == ESP_OK) playback_channel_enabled = false;
        }
        memset(playback_scratch, 0, sizeof(playback_scratch));
        size_t cleared = 0;
        int64_t deadline = esp_timer_get_time() + PLAYBACK_BLOCK_TIMEOUT_US;
        if (dma_capacity == 0) result = ESP_ERR_INVALID_STATE;
        while (result == ESP_OK && cleared < dma_capacity) {
            if (esp_timer_get_time() >= deadline) { result = ESP_ERR_TIMEOUT; break; }
            size_t size = dma_capacity - cleared;
            if (size > sizeof(playback_scratch)) size = sizeof(playback_scratch);
            size_t loaded = 0;
            result = i2s_channel_preload_data(playback_channel, playback_scratch, size, &loaded);
            if (result == ESP_OK && (loaded == 0 || loaded > size || (loaded & 1))) result = ESP_ERR_INVALID_STATE;
            cleared += loaded;
        }
        if (result == ESP_OK) {
            result = i2s_channel_enable(playback_channel);
            if (result == ESP_OK) playback_channel_enabled = true;
        }
        xSemaphoreTake(playback_mutex, portMAX_DELAY);
        if (result != ESP_OK) {
            // A partial preload cannot safely be retried from an unknown DMA
            // cursor. Keep the channel stopped; require a device reset.
            playback_stop_failed = playback_error = true;
            playback_reset_pending = false;
            ++playback_stats.write_errors;
        } else if (generation == playback_generation) {
            playback_reset_pending = false;
            if (playback_cancel_pending) {
                portENTER_CRITICAL(&dma_mux);
                playback_cancel_start = dma_eof_count;
                size_t size = dma_event_bytes;
                portEXIT_CRITICAL(&dma_mux);
                playback_cancel_events = size ? 2 * ((dma_capacity + size - 1) / size) + 2 : 0;
                playback_cancel_deadline = esp_timer_get_time() + PLAYBACK_CANCEL_TIMEOUT_US;
                playback_cancel_draining = true;
            }
        }
        xSemaphoreGive(playback_mutex);
        if (result != ESP_OK) {
            ESP_LOGE("playback", "I2S stop/reset failed; device reset required");
            return -1;
        }
    }
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    int result = playback_reset_pending ? 0 : 1;
    if (playback_cancel_pending) {
        result = 0;
        if (playback_cancel_draining && !playback_cancel_ack_ready) {
            portENTER_CRITICAL(&dma_mux);
            uint32_t count = dma_eof_count;
            size_t size = dma_event_bytes;
            portEXIT_CRITICAL(&dma_mux);
            if (playback_cancel_events == 0 && size) {
                playback_cancel_start = count;
                playback_cancel_events = 2 * ((dma_capacity + size - 1) / size) + 2;
            }
            if (esp_timer_get_time() >= playback_cancel_deadline) {
                playback_error = true;
                ++playback_stats.write_errors;
                result = -1;
            } else if (playback_cancel_events &&
                       (uint32_t)(count - playback_cancel_start) >= playback_cancel_events) {
                playback_cancel_ack_ready = true;
            }
        }
    }
    xSemaphoreGive(playback_mutex);
    return result;
}

int playback_step(void) {
    int control = playback_control_step();
    if (control <= 0) return control;
    size_t size = 0;
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    uint32_t generation = playback_generation;
    uint8_t *item = NULL;
    if (playback_connected && !playback_error) {
        item = xRingbufferReceiveUpTo(playback_ring, &size, 0, PLAYBACK_BLOCK_BYTES);
    }
    if (item == NULL) {
        if (playback_active) ++playback_stats.starvation;
        playback_active = false;
        playback_drain_locked();
        xSemaphoreGive(playback_mutex);
        return 0; // Start immediately when data returns; no EOS/ACK inferred.
    }
    playback_stats.queued_bytes -= size;
    playback_active = true;
    playback_busy = true;
    // BYTEBUF permits only one outstanding receive. Copy and return under the
    // mutex, so reset can drain the queue while this block is in I2S write.
    memcpy(playback_scratch, item, size);
    vRingbufferReturnItem(playback_ring, item);
    item = playback_scratch;
    xSemaphoreGive(playback_mutex);

    size_t written = 0;
    bool failed = false;
    int64_t deadline = esp_timer_get_time() + PLAYBACK_BLOCK_TIMEOUT_US;
    while (written < size && playback_current(generation)) {
        if (esp_timer_get_time() >= deadline) { failed = true; break; }
        size_t count = 0;
        esp_err_t error = i2s_channel_write(playback_channel, item + written,
            size - written, &count, PLAYBACK_WRITE_TIMEOUT_MS);
        if ((error != ESP_OK && error != ESP_ERR_TIMEOUT) || count > size - written || (count & 1) ||
            (error == ESP_OK && count == 0)) {
            failed = true;
            break;
        }
        written += count; // A timed-out write may still have accepted samples.
        if (count == 0) vTaskDelay(pdMS_TO_TICKS(10));
    }
    xSemaphoreTake(playback_mutex, portMAX_DELAY);
    playback_stats.bytes_written += written;
    playback_busy = false;
    if (failed && playback_generation == generation && playback_connected) {
        playback_error = true;
        ++playback_stats.write_errors;
    }
    xSemaphoreGive(playback_mutex);
    if (failed) ESP_LOGE("playback", "I2S block failed or exceeded deadline");
    return failed ? -1 : 1;
}

void playback_task(void *argument) {
    (void)argument;
    while (1) {
        if (playback_step() <= 0) vTaskDelay(pdMS_TO_TICKS(10));
    }
}
