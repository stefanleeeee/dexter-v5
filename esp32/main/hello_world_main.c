#include "dexter_local_config.h"
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#include <stdbool.h>
#include <fcntl.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/ringbuf.h"
#include "freertos/semphr.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "lwip/sockets.h"
#include "driver/i2s_std.h"
#include "playback.h"






static const char *TAG = "jarvis_node";
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

RingbufHandle_t audio_ringbuf;
static SemaphoreHandle_t mic_mutex;
static bool mic_connected = false;
static uint32_t mic_generation = 1;
static uint32_t mic_drops;
// Fresh capture state: protected by the existing mic mutex.
enum { MIC_FRESH_IDLE, MIC_FRESH_DRAIN, MIC_FRESH_READY, MIC_FRESH_ERROR };
static unsigned mic_fresh_phase;
static bool mic_fresh_sticky_error;


i2s_chan_handle_t tx_chan; 
i2s_chan_handle_t rx_chan; 

static void event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        esp_wifi_connect();
        ESP_LOGI(TAG, "Retrying connection to AP");
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

void wifi_init_sta(void) {
    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, NULL);
    wifi_config_t wifi_config = { .sta = { .ssid = WIFI_SSID, .password = WIFI_PASS }, };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdFALSE, portMAX_DELAY);
}

void i2s_init(void) {
    // ДИНАМИК (TX) - 24000 Гц
    i2s_chan_config_t tx_chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    // Without this, the circular DMA descriptors can replay old PCM while idle.
    tx_chan_cfg.auto_clear_after_cb = true;
    ESP_ERROR_CHECK(i2s_new_channel(&tx_chan_cfg, &tx_chan, NULL));

    i2s_std_config_t tx_std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(24000), 
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = GPIO_NUM_16,
            .ws   = GPIO_NUM_17,
            .dout = GPIO_NUM_18,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx_chan, &tx_std_cfg));
    ESP_ERROR_CHECK(playback_bind_i2s(tx_chan));
    ESP_ERROR_CHECK(i2s_channel_enable(tx_chan));

    // МИКРОФОН (RX) - 16000 Гц
    i2s_chan_config_t rx_chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&rx_chan_cfg, NULL, &rx_chan));

    i2s_std_config_t rx_std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(16000), 
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = GPIO_NUM_4,
            .ws   = GPIO_NUM_5,
            .dout = I2S_GPIO_UNUSED,
            .din  = GPIO_NUM_6,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    // Жестко привязываем к левому слоту, чтобы не было удвоения голоса (эффекта демона)
    rx_std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_chan, &rx_std_cfg));
    ESP_ERROR_CHECK(i2s_channel_enable(rx_chan));

    ESP_LOGI(TAG, "I2S Hardware init OK");
}

// Microphone generation is captured BEFORE I2S read; an in-flight sample from
// the previous connection must not be queued after reconnect.
static uint32_t mic_capture_generation(void) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    uint32_t generation = mic_connected && mic_fresh_phase == MIC_FRESH_IDLE ? mic_generation : 0;
    xSemaphoreGive(mic_mutex);
    return generation;
}

static void mic_set_connected(bool connected) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    mic_connected = false;
    mic_fresh_phase = MIC_FRESH_IDLE;
    if (++mic_generation == 0) ++mic_generation; // Zero means disconnected.
    size_t size;
    void *item;
    while ((item = xRingbufferReceive(audio_ringbuf, &size, 0)) != NULL) {
        vRingbufferReturnItem(audio_ringbuf, item);
    }
    mic_connected = connected;
    xSemaphoreGive(mic_mutex);
}

static BaseType_t mic_enqueue(const void *pcm, size_t size, uint32_t generation) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    BaseType_t result = pdTRUE; // Disconnected/stale audio is deliberately dropped.
    if (mic_connected && mic_fresh_phase == MIC_FRESH_IDLE && generation != 0 && generation == mic_generation) {
        result = xRingbufferSend(audio_ringbuf, pcm, size, pdMS_TO_TICKS(10));
        if (result != pdTRUE) ++mic_drops;
    }
    xSemaphoreGive(mic_mutex);
    return result;
}

// Only the microphone producer calls RX driver APIs, never TCP or a mutex owner.
static bool mic_request_fresh(void) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    bool ok = mic_connected && mic_fresh_phase == MIC_FRESH_IDLE && !mic_fresh_sticky_error;
    if (ok) {
        if (++mic_generation == 0) ++mic_generation;
        size_t size;
        void *item;
        while ((item = xRingbufferReceive(audio_ringbuf, &size, 0)) != NULL)
            vRingbufferReturnItem(audio_ringbuf, item);
        mic_fresh_phase = MIC_FRESH_DRAIN;
    }
    xSemaphoreGive(mic_mutex);
    return ok;
}

static unsigned mic_fresh_status(void) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    unsigned phase = mic_fresh_phase;
    xSemaphoreGive(mic_mutex);
    return phase;
}

static void mic_fresh_release(void) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    if (mic_fresh_phase == MIC_FRESH_READY) mic_fresh_phase = MIC_FRESH_IDLE;
    xSemaphoreGive(mic_mutex);
}

static void mic_service_fresh(void) {
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    bool requested = mic_connected && mic_fresh_phase == MIC_FRESH_DRAIN;
    uint32_t generation = mic_generation;
    xSemaphoreGive(mic_mutex);
    if (!requested) return;
    // Acoustic tail protection, NOT the freshness proof. No barge-in/AEC.
    vTaskDelay(pdMS_TO_TICKS(300));
    i2s_chan_info_t info;
    bool ok = i2s_channel_get_info(rx_chan, &info) == ESP_OK && info.total_dma_buf_size > 0;
    if (ok) ok = i2s_channel_disable(rx_chan) == ESP_OK;
    if (ok) ok = i2s_channel_enable(rx_chan) == ESP_OK;
    // Drain more than the entire RX DMA capacity after restart. This also
    // consumes old software-queued descriptors; no old DMA/sample can cross READY.
    uint8_t discard[1024];
    size_t remaining = ok ? info.total_dma_buf_size + sizeof(discard) : 0;
    int64_t deadline = esp_timer_get_time() + 1000000;
    while (ok && remaining) {
        size_t received = 0;
        size_t requested_bytes = remaining < sizeof(discard) ? remaining : sizeof(discard);
        esp_err_t err = i2s_channel_read(rx_chan, discard, requested_bytes, &received, 20);
        if (esp_timer_get_time() >= deadline ||
            (err != ESP_OK && err != ESP_ERR_TIMEOUT) || received > requested_bytes || received % 4) {
            ok = false;
        } else {
            remaining -= received;
        }
    }
    xSemaphoreTake(mic_mutex, portMAX_DELAY);
    if (!ok) mic_fresh_sticky_error = true; // Unknown RX driver state requires Reset.
    if (mic_connected && mic_generation == generation && mic_fresh_phase == MIC_FRESH_DRAIN)
        mic_fresh_phase = ok ? MIC_FRESH_READY : MIC_FRESH_ERROR;
    xSemaphoreGive(mic_mutex);
}

static void mic_i2s_task(void *pvParameters) {
    int32_t raw_buf[256]; 
    int16_t pcm_buf[256];
    size_t bytes_read = 0;

    ESP_LOGI(TAG, "I2S Mic sampler START (Continuous Stream)");

    while (1) {
        mic_service_fresh();
        uint32_t generation = mic_capture_generation();
        esp_err_t err = i2s_channel_read(rx_chan, raw_buf, sizeof(raw_buf), &bytes_read, 50);
        
        if (err == ESP_OK && bytes_read > 0) {
            int samples = bytes_read / 4; 
            for (int i = 0; i < samples; i++) {
                // Извлекаем старшие 16 бит из 24-битного потока (деление гарантирует сохранение знака)[cite: 13]
                pcm_buf[i] = (int16_t)(raw_buf[i] / 65536); 
            }

            {
                BaseType_t res = mic_enqueue(pcm_buf, samples * 2, generation);
                if (res != pdTRUE) {
                    ESP_LOGW(TAG, "[DIAG] RingBuffer FULL! Dropping mic packet.");
                }
            }
        } else {
            ESP_LOGE(TAG, "[I2S] Mic read error or 0 bytes: %d", err);
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

// TCP I/O limits: per frame, not reset by successful trickle traffic.
static bool followup_probe_pending;
static bool fresh_pending, fresh_done_valid;
static uint8_t fresh_token[32], fresh_done_token[32];
static int64_t fresh_deadline;

#define TCP_IO_TIMEOUT_US 10000000LL
#define TCP_CONNECT_TIMEOUT_US 10000000LL
#define TCP_RETRY_MS 2000

static struct {
    uint32_t remaining;
    size_t buffered;
    int64_t deadline;
    uint8_t data[PLAYBACK_BLOCK_BYTES];
} audio_rx;
static struct {
    uint8_t type;
    size_t position;
    int64_t deadline;
    uint8_t token[PLAYBACK_TOKEN_BYTES];
} control_rx;

static int tcp_send_all(int fd, const uint8_t *data, size_t length, int64_t deadline) {
    size_t sent = 0;
    while (sent < length) {
        if (esp_timer_get_time() >= deadline) return -1;
        int count = send(fd, data + sent, length - sent, MSG_DONTWAIT);
        if (count > 0) {
            sent += (size_t)count;
        } else if (count < 0 && errno == EINTR) {
            continue;
        } else if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            vTaskDelay(pdMS_TO_TICKS(10));
        } else {
            return -1;
        }
    }
    return esp_timer_get_time() >= deadline ? -1 : 0;
}

static int tcp_send_mic(int fd, const uint8_t *data, size_t length, int64_t deadline) {
    if (length == 0 || length > 32000 || (length & 1)) return -1;
    uint8_t header[5] = {0x02, (uint8_t)(length >> 24),
                         (uint8_t)(length >> 16), (uint8_t)(length >> 8),
                         (uint8_t)length};
    if (tcp_send_all(fd, header, sizeof(header), deadline) != 0) return -1;
    return tcp_send_all(fd, data, length, deadline);
}

static int tcp_play_audio(int fd, uint32_t payload_len) {
    (void)fd;
    if (payload_len > 16384 || (payload_len & 1)) {
        ESP_LOGE(TAG, "Invalid PCM payload length: %lu", (unsigned long)payload_len);
        return -1;
    }
    audio_rx.remaining = payload_len;
    audio_rx.buffered = 0;
    audio_rx.deadline = esp_timer_get_time() + TCP_IO_TIMEOUT_US;
    return 0;
}

// At most one non-blocking recv per poll. Queue pressure pauses only downlink;
// the caller continues mic uplink between polls. No I2S calls in this task.
static int tcp_audio_poll(int fd) {
    if (playback_failed() || esp_timer_get_time() >= audio_rx.deadline) return -1;
    if (audio_rx.buffered == PLAYBACK_BLOCK_BYTES || (audio_rx.remaining == 0 && audio_rx.buffered)) {
        int queued = playback_enqueue(audio_rx.data, audio_rx.buffered);
        if (queued <= 0) return queued < 0 ? -1 : 0;
        audio_rx.buffered = 0;
    }
    if (audio_rx.remaining == 0) return 1;
    size_t room = PLAYBACK_BLOCK_BYTES - audio_rx.buffered;
    if (room > audio_rx.remaining) room = audio_rx.remaining;
    int count = recv(fd, audio_rx.data + audio_rx.buffered, room, MSG_DONTWAIT);
    if (count > 0) {
        if (esp_timer_get_time() >= audio_rx.deadline) return -1;
        audio_rx.buffered += (size_t)count;
        audio_rx.remaining -= (uint32_t)count;
        if (audio_rx.buffered == PLAYBACK_BLOCK_BYTES || audio_rx.remaining == 0) {
            int queued = playback_enqueue(audio_rx.data, audio_rx.buffered);
            if (queued < 0) return -1;
            if (queued > 0) audio_rx.buffered = 0;
        }
        return audio_rx.remaining == 0 && audio_rx.buffered == 0 ? 1 : 0;
    }
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    return -1;
}

static int tcp_handle_message(int fd, const uint8_t header[5]) {
    uint32_t length = ((uint32_t)header[1] << 24) | ((uint32_t)header[2] << 16) |
                      ((uint32_t)header[3] << 8) | (uint32_t)header[4];
    if (header[0] == 0x17 && length == 0) {
        followup_probe_pending = true;
        return 0;
    }
    if (header[0] == 0x12) return tcp_play_audio(fd, length);
    if ((header[0] == 0x11 || header[0] == 0x13 || header[0] == 0x15 || header[0] == 0x19) && length == PLAYBACK_TOKEN_BYTES) {
        control_rx.type = header[0];
        control_rx.position = 0;
        control_rx.deadline = esp_timer_get_time() + TCP_IO_TIMEOUT_US;
        return 0;
    }
    // START/STOP (and previously ignored types) have no payload. Reject an
    // unknown body instead of interpreting its bytes as the next header.
    if (length != 0) {
        ESP_LOGE(TAG, "Unexpected payload for message type: %u", (unsigned)header[0]);
        return -1;
    }
    return 0;
}

static int tcp_control_poll(int fd) {
    if (esp_timer_get_time() >= control_rx.deadline) return -1;
    int count = recv(fd, control_rx.token + control_rx.position,
                     PLAYBACK_TOKEN_BYTES - control_rx.position, MSG_DONTWAIT);
    if (count > 0) {
        control_rx.position += (size_t)count;
        if (esp_timer_get_time() >= control_rx.deadline) return -1;
        if (control_rx.position == PLAYBACK_TOKEN_BYTES) {
            if (control_rx.type == 0x19) {
                bool valid = fresh_done_valid && !fresh_pending &&
                    memcmp(control_rx.token, fresh_done_token, 32) == 0;
                if (valid && mic_request_fresh()) {
                    memcpy(fresh_token, control_rx.token, 32);
                    fresh_pending = true;
                    fresh_done_valid = false; // A response ID is usable only once.
                    fresh_deadline = esp_timer_get_time() + 2000000;
                    ESP_LOGI(TAG, "[FRESH CAPTURE REQUESTED]");
                }
                control_rx.type = 0;
                return 1; // Stale/unready request never changes capture generation.
            }
            bool cancel = control_rx.type == 0x15;
            bool valid = cancel ? playback_cancel(control_rx.token) :
                (control_rx.type == 0x11 ? playback_begin(control_rx.token) : playback_end(control_rx.token));
            if ((cancel || control_rx.type == 0x11) && valid) fresh_done_valid = false;
            if (cancel && valid) audio_rx.remaining = audio_rx.buffered = 0;
            if (valid) ESP_LOGI(TAG, "[PLAYBACK %s] %02x%02x%02x%02x",
                cancel ? "CANCEL" : (control_rx.type == 0x11 ? "START" : "EOS"), control_rx.token[28], control_rx.token[29],
                control_rx.token[30], control_rx.token[31]);
            control_rx.type = 0;
            return valid || cancel ? 1 : -1; // Stale cancellation never affects a newer response.
        }
        return 0;
    }
    if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return 0;
    return -1;
}

static int tcp_send_playback_ack(int fd) {
    uint8_t token[PLAYBACK_TOKEN_BYTES];
    uint8_t type = 0x16;
    if (!playback_take_cancel_ack(token)) {
        type = 0x14;
        if (!playback_take_ack(token)) return 0;
    }
    uint8_t message[5 + PLAYBACK_TOKEN_BYTES] = {type, 0, 0, 0, PLAYBACK_TOKEN_BYTES};
    memcpy(message + 5, token, PLAYBACK_TOKEN_BYTES);
    int result = tcp_send_all(fd, message, sizeof(message), esp_timer_get_time() + TCP_IO_TIMEOUT_US);
    if (result == 0 && type == 0x14) {
        memcpy(fresh_done_token, token, 32);
        fresh_done_valid = true;
    }
    if (result == 0) ESP_LOGI(TAG, "[PLAYBACK %s] %02x%02x%02x%02x", type == 0x16 ? "CANCELLED" : "ACK", token[28], token[29], token[30], token[31]);
    return result;
}

static int tcp_send_followup(int fd) {
    if (followup_probe_pending) {
        uint8_t message[5] = {0x18, 0, 0, 0, 0};
        if (tcp_send_all(fd, message, sizeof(message), esp_timer_get_time() + TCP_IO_TIMEOUT_US) != 0) return -1;
        followup_probe_pending = false;
        ESP_LOGI(TAG, "[FOLLOWUP SUPPORTED] v1");
    }
    if (!fresh_pending) return 0;
    unsigned phase = mic_fresh_status();
    if (phase == MIC_FRESH_ERROR || esp_timer_get_time() >= fresh_deadline) return -1;
    if (phase == MIC_FRESH_READY) {
        uint8_t message[37] = {0x1A, 0, 0, 0, 32};
        memcpy(message + 5, fresh_token, 32);
        if (tcp_send_all(fd, message, sizeof(message), fresh_deadline) != 0) return -1;
        fresh_pending = false;
        mic_fresh_release(); // Only post-fence PCM can follow READY on this writer.
        ESP_LOGI(TAG, "[FRESH CAPTURE CONFIRMED]");
    }
    return 0;
}

// Poll one header fragment so mic uplink continues while the server is silent.
// No idle-downlink timeout: STT/LLM/TTS may legitimately take longer than 10 s.
static int tcp_receive_header(int fd, uint8_t header[5], int *position, int64_t *started) {
    if (*position && esp_timer_get_time() - *started >= TCP_IO_TIMEOUT_US) return -1;
    int count = recv(fd, header + *position, 5 - *position, 0);
    if (count > 0) {
        if (*position == 0) *started = esp_timer_get_time();
        *position += count;
        if (esp_timer_get_time() - *started >= TCP_IO_TIMEOUT_US) return -1;
        return *position == 5 ? 1 : 0;
    }
    if (count == 0) return -1;
    if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) return -1;
    return (*position && esp_timer_get_time() - *started >= TCP_IO_TIMEOUT_US) ? -1 : 0;
}

static int tcp_connect_with_timeout(int fd, const struct sockaddr *address, socklen_t size) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) return -1;
    int64_t deadline = esp_timer_get_time() + TCP_CONNECT_TIMEOUT_US;
    int result = connect(fd, address, size);
    if (result != 0 && errno != EINPROGRESS && errno != EWOULDBLOCK) return -1;
    while (result != 0) {
        int64_t remaining = deadline - esp_timer_get_time();
        if (remaining <= 0) return -1;
        fd_set writable;
        FD_ZERO(&writable);
        FD_SET(fd, &writable);
        struct timeval timeout = {.tv_sec = (long)(remaining / 1000000),
                                  .tv_usec = (long)(remaining % 1000000)};
        int ready = select(fd + 1, NULL, &writable, NULL, &timeout);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) return -1;
        int error = 0;
        socklen_t length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) != 0 || error != 0) return -1;
        result = 0;
    }
    if (esp_timer_get_time() >= deadline) return -1;
    return fcntl(fd, F_SETFL, flags) < 0 ? -1 : 0;
}

static void tcp_client_task(void *pvParameters) {
    struct sockaddr_in dest_addr;
    dest_addr.sin_addr.s_addr = inet_addr(HOST_IP_ADDR);
    dest_addr.sin_family = AF_INET;
    dest_addr.sin_port = htons(PORT);

    while (1) {
        mic_set_connected(false);
        followup_probe_pending = fresh_pending = fresh_done_valid = false;
        playback_set_connected(false);
        audio_rx.remaining = 0;
        audio_rx.buffered = 0;
        control_rx.type = 0;
        xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdFALSE, portMAX_DELAY);
        int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
        if (sock < 0) {
            vTaskDelay(pdMS_TO_TICKS(TCP_RETRY_MS));
            continue;
        }
        
        struct timeval timeout;
        timeout.tv_sec = 0;
        timeout.tv_usec = 50000; // Таймаут приема 50мс
        int options_ok = setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

        if (options_ok != 0 || tcp_connect_with_timeout(sock, (struct sockaddr *)&dest_addr, sizeof(dest_addr)) != 0 ||
            !(xEventGroupGetBits(s_wifi_event_group) & WIFI_CONNECTED_BIT)) {
            ESP_LOGW(TAG, "TCP Connect failed. Retrying...");
            close(sock); sock = -1;
            vTaskDelay(pdMS_TO_TICKS(TCP_RETRY_MS));
            continue;
        }
        mic_set_connected(true);
        playback_set_connected(true);
        ESP_LOGI(TAG, "TCP Connected. Streaming AUDIO continuously...");
        ESP_LOGI(TAG, "[DIAG] Free Heap: %lu | Min Free Heap: %lu", 
                 (unsigned long)esp_get_free_heap_size(), 
                 (unsigned long)esp_get_minimum_free_heap_size());
        ESP_LOGI(TAG, "[DIAG] TCP Task Stack HWM: %u bytes", 
                 (unsigned int)uxTaskGetStackHighWaterMark(NULL));
        
        uint8_t rx_header[5];
        int rx_pos = 0;
        int64_t header_started = 0;
        int64_t next_diagnostics = esp_timer_get_time() + 5000000;

        while (1) {
            if (!(xEventGroupGetBits(s_wifi_event_group) & WIFI_CONNECTED_BIT)) break;
            if (playback_failed()) break;
            if (tcp_send_playback_ack(sock) != 0) break;
            if (tcp_send_followup(sock) != 0) break;
            if (control_rx.type) {
                if (tcp_control_poll(sock) < 0) break;
            } else if (audio_rx.remaining || audio_rx.buffered) {
                if (tcp_audio_poll(sock) < 0) break;
            } else {
                int status = tcp_receive_header(sock, rx_header, &rx_pos, &header_started);
                if (status < 0) break;
                if (status == 1) {
                    if (tcp_handle_message(sock, rx_header) != 0) break;
                    rx_pos = 0;
                }
            }

            // Отправка микрофона на сервер
            size_t item_size;
            uint8_t *item = (uint8_t *)xRingbufferReceive(audio_ringbuf, &item_size, pdMS_TO_TICKS(10));
            if (item != NULL && sock != -1) {
                int64_t deadline = esp_timer_get_time() + TCP_IO_TIMEOUT_US;
                if (rx_pos && header_started + TCP_IO_TIMEOUT_US < deadline) {
                    deadline = header_started + TCP_IO_TIMEOUT_US;
                }
                if (control_rx.type && control_rx.deadline < deadline) deadline = control_rx.deadline;
                int result = tcp_send_mic(sock, item, item_size, deadline);
                vRingbufferReturnItem(audio_ringbuf, (void *)item);
                if (result != 0) break;
            }
            if (esp_timer_get_time() >= next_diagnostics) {
                playback_stats_t stats;
                playback_snapshot(&stats);
                xSemaphoreTake(mic_mutex, portMAX_DELAY);
                uint32_t drops = mic_drops;
                xSemaphoreGive(mic_mutex);
                ESP_LOGI(TAG, "[AUDIO] queue=%lu/%u high=%lu pressure=%lu empty=%lu i2s_bytes=%lu errors=%lu mic_drop=%lu heap=%lu",
                    (unsigned long)stats.queued_bytes, (unsigned)PLAYBACK_QUEUE_BYTES,
                    (unsigned long)stats.high_water_bytes, (unsigned long)stats.backpressure,
                    (unsigned long)stats.starvation, (unsigned long)stats.bytes_written,
                    (unsigned long)stats.write_errors, (unsigned long)drops,
                    (unsigned long)esp_get_free_heap_size());
                next_diagnostics = esp_timer_get_time() + 5000000;
            }
        }
        mic_set_connected(false);
        playback_set_connected(false);
        if (sock != -1) { close(sock); sock = -1; }
        vTaskDelay(pdMS_TO_TICKS(TCP_RETRY_MS));
    }
}

void app_main(void) {
    esp_reset_reason_t reason = esp_reset_reason();
    ESP_LOGW(TAG, "=== SYSTEM STARTUP ===");
    ESP_LOGW(TAG, "Reset reason: %d", reason);
    ESP_LOGI(TAG, "Free heap at boot: %lu bytes", (unsigned long)esp_get_free_heap_size());
    nvs_flash_init();
    wifi_init_sta();
    i2s_init();

    audio_ringbuf = xRingbufferCreate(16384, RINGBUF_TYPE_BYTEBUF);
    if (audio_ringbuf == NULL) return;
    mic_mutex = xSemaphoreCreateMutex();
    if (mic_mutex == NULL) {
        vRingbufferDelete(audio_ringbuf);
        return;
    }
    if (!playback_init(tx_chan)) {
        vSemaphoreDelete(mic_mutex);
        vRingbufferDelete(audio_ringbuf);
        ESP_LOGE(TAG, "Playback queue initialization failed");
        return;
    }
    if (xTaskCreate(playback_task, "playback", 4096, NULL, 5, NULL) != pdPASS) {
        playback_deinit();
        vSemaphoreDelete(mic_mutex);
        vRingbufferDelete(audio_ringbuf);
        ESP_LOGE(TAG, "Playback task creation failed");
        return;
    }

    xTaskCreate(mic_i2s_task, "mic_i2s", 4096, NULL, 6, NULL); 
    xTaskCreate(tcp_client_task, "tcp_client", 4096, NULL, 5, NULL); 
}
