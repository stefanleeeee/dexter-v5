idf_component_register(SRCS "hello_world_main.c" "playback.c"
                       INCLUDE_DIRS ""
                       PRIV_REQUIRES esp_driver_gpio esp_driver_i2s esp_ringbuf esp_wifi esp_event nvs_flash esp_netif lwip esp_adc esp_timer)
