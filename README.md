# AI Dexter — Phase 3 Streaming Voice Responses

A voice assistant using an ESP32-S3 microphone/speaker and a Python server. Vosk detects “Декстер”; Groq provides command transcription and LLM replies; local Silero V5.6 synthesizes Russian speech on CPU. Phases 1–3 are complete and accepted; Phase 4 dialogue context/natural conversation is next, not implemented. Internet and a Groq API key are required for STT/LLM. CosyVoice, voice barge-in, Windows actions and acoustic echo cancellation are not implemented.

## Architecture

ESP I2S microphone → PCM16LE mono 16 kHz → TCP → Python/Vosk wake word → Groq Whisper STT → Groq LLM streaming → completed-phrase buffer → phonetic normalization → Silero V5.6 CPU (selectable voice) → PCM16LE mono 24 kHz → TCP → bounded ESP playback queue → I2S speaker.

TCP: 5-byte header `[type:u8][length:u32 big-endian]`; raw PCM, no WAV headers. MIC 0x02, START 0x11, PCM 0x12, EOS 0x13, DONE 0x14, CANCEL 0x15, CANCELLED 0x16. Control IDs are 32 bytes (connection UUID + counter). Update server/ESP as a matched pair. DONE follows DMA drain; cancellation clears software/DMA output before CANCELLED. See ARCHITECTURE.md.

## Python setup (Windows / PowerShell)

The recorded working environment is CPython 3.14.3, CPU-only torch 2.15.0. The exact installed dependency snapshot is requirements.txt; other platforms/version combinations are not yet validated. If the Windows py launcher is unavailable, use the executable of your installed CPython 3.14 to create the virtual environment; do not substitute an unrelated PATH interpreter silently.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set `GROQ_API_KEY`, listening `HOST` (normally `0.0.0.0`) and `PORT`. OS environment variables override .env. No python-dotenv dependency is needed. Only KEY=VALUE lines, whole-line comments, literal values, single-quoted strings or JSON double-quoted strings are supported; there is no variable interpolation or inline comment syntax. Never commit the filled file. Invalid/missing required configuration fails without printing its value. Normal logs redact configured API keys and IPv4 addresses, but recognized speech/LLM content remains private user data; inspect logs before sharing.

Place a trusted Russian Vosk model directory at `model_ru/` and **the official
Silero package at `v5_6_ru.pt` in the repository root, next to `server.py`**.
They are local prerequisites, not included in Git. Sources:
[Vosk model catalog](https://alphacephei.com/vosk/models),
[Silero model manifest](https://github.com/snakers4/silero-models/blob/e382298387332f2bf461fe6eadfde99104611b0c/models.yml).
The official Silero file URL is `https://models.silero.ai/models/tts/ru/v5_6_ru.pt`.
Respect model licenses. Loading a torch package executes package code: use a
trusted source and preserve provenance. The tested package size is 147664410
bytes, SHA256 `04cc9b1d08ce38bcaa9d02c488d80d3e6806d17f7eaa7c95982f36a3cbf50b2e`;
this is a recorded local hash, not an independently published signed checksum.
The server does not download weights automatically and fails if the selected
file is missing. Do not substitute a desktop installer or a different `.bin`.

The safe `.env.example` selects `TTS_MODEL=v5_6_ru`, `TTS_VOICE=aidar` and
`TTS_NORMALIZE_EN=true`. Set the startup speaker and normalization locally.
For optional V4 rollback, provide your own trusted `v4_ru.pt`, set
`TTS_MODEL=v4_ru` and restart. Both models are excluded from Git. Do not load both
models at once. Python/PyTorch dependencies have not been changed for this release.


Start from the repository root:

```powershell
.\.venv\Scripts\python.exe -B server.py
```

The server listens on the configured port. Configure firewall/LAN access for the ESP. TCP is plain, unauthenticated LAN transport; do not expose it directly to the public internet. Type `cancel` or `отмена` + Enter in the server console to cancel the current response. In-flight model calls finish naturally; their canceled results are not sent. No microphone voice interruption is implemented.

Use `voice list`, `voice status`, or `voice eugene` / `voice aidar` /
`voice kseniya` / `voice baya` / `voice xenia`. Switching does not reload the model
or change the voice mid-response. Console selection is session-local; model
selection requires a restart. Known English technical names are adapted only in
a TTS copy (for example Python → Пайтон, GitHub → Гитхаб). Unknown Latin and
protected literals are retained; their pronunciation is not guaranteed.
See [TTS_SETTINGS.md](TTS_SETTINGS.md) for limits and rollback.


## Настройка потоковой генерации LLM

Конфигурация читается при старте: после изменения параметров перезапустите сервер.
Команды PowerShell ниже действуют для текущей сессии терминала (и запускаемых из
неё процессов). Переменные окружения имеют приоритет над локальной `.env`.
Для постоянных настроек используйте игнорируемую `.env`; не добавляйте её в Git.

| Параметр | Рекомендуется | Альтернативы |
|---|---|---|
| LLM_STREAMING | true | false |
| LLM_MAX_COMPLETION_TOKENS | 2048 | 3072, 4096 |
| LLM_REASONING_EFFORT | low | medium, high |
| LLM_INCLUDE_REASONING | false | true |

- `LLM_STREAMING`: true включает Groq deltas → готовые фразы → последовательный
  Silero CPU → PCM; false возвращает режим полного ответа. Парсер также принимает
  1/0. Streaming ускоряет возможность начать озвучивание, но не обязательно
  завершение всего ответа. Однофразовый ответ может не получить выигрыша.
- `LLM_MAX_COMPLETION_TOKENS`: бюджет генерации одного ответа, включая видимый
  текст и reasoning-токены. Локальный парсер принимает целое1..32768; это защитный
  диапазон конфигурации, не утверждение о максимальном контексте модели.
  Рекомендуется2048, для длинных ответов3072/4096. Увеличение бюджета не означает
  автоматического расхода всех токенов; фактический расход и длительность ответа
  могут вырасти. Usage учитывается по данным API, отсутствующие значения — null.
- `LLM_REASONING_EFFORT`: low/medium/high для текущей openai/gpt-oss-120b.
  Low предназначен для быстрых повседневных ответов; medium/high могут тратить
  больше токенов/времени на сложные рассуждения. Это не гарантия фиксированной
  задержки или качества. Настройка применяется также в режиме полного ответа.
- `LLM_INCLUDE_REASONING`: false/true (также0/1) управляет возвратом reasoning-поля
  в ответе API. False рекомендуется голосовому ассистенту и **не отключает само
  вычисление reasoning**. Даже при true Dexter не озвучивает и не логирует это
  поле: пользовательским текстом считается только content. Несовместимый
  reasoning_format вместе с include_reasoning не передаётся.

Пример из корня проекта после остановки старого сервера через Ctrl+C:

```powershell
# Включить потоковую генерацию ответов.
$env:LLM_STREAMING = 'true'

# Максимальный бюджет генерации одного ответа.
# Включает видимый текст и reasoning-токены.
# Рекомендуемое значение: 2048.
# Для более длинных ответов можно попробовать 3072 или 4096.
$env:LLM_MAX_COMPLETION_TOKENS = '2048'

# Усилие рассуждения модели.
# low — быстрые повседневные ответы.
# medium — более сложные задачи.
# high — более глубокие рассуждения, потенциально дольше.
$env:LLM_REASONING_EFFORT = 'low'

# Включать ли reasoning-содержимое в ответ API.
# false — рекомендуемое значение для голосового ассистента.
# Не отключает само вычисление reasoning.
$env:LLM_INCLUDE_REASONING = 'false'

# Запуск Dexter.
& '.\.venv\Scripts\python.exe' -B '.\server.py'
```

Эти значения уже заданы в безопасном `.env.example`. При `finish_reason=stop`
нормальный исход — done; `length` с текстом — partial_length с выводом полученного
текста и завершением его PCM через EOS/ACK, без автоматического CANCEL только из-за
лимита. Такой ответ не добавляется в историю как полностью завершённый.
`empty_length` не запускает пустое воспроизведение; cancelled/error различаются.
Нет автоматического продолжения ответа дополнительными запросами.
Полные ограничения и определения метрик: [STREAMING.md](STREAMING.md).

## ESP32-S3 setup

Tested with ESP-IDF v6.1 and target esp32s3. In the ESP-IDF terminal:

```powershell
Copy-Item esp32/main/dexter_local_config.example.h esp32/main/dexter_local_config.h
# Edit the local header: Wi-Fi SSID/password, server LAN IPv4 address and matching port.
cd esp32
idf.py set-target esp32s3
idf.py build
# Only when ready to update the board, substitute its serial port:
idf.py -p YOUR_SERIAL_PORT flash monitor
```

sdkconfig.defaults records the tested configuration (PSRAM disabled); generated sdkconfig/build are local. The local header is required, ignored by Git and never distributed in binaries: compiled firmware embeds Wi-Fi credentials. Do not publish filled configs or firmware build artifacts.

Wiring: microphone I2S1, BCLK=4, WS=5, DIN=6, 16 kHz / left 32-bit slot converted to PCM16. Speaker I2S0, BCLK=16, WS=17, DOUT=18, Philips mono PCM16 / 24 kHz. Physical microphone/amplifier models and board-specific electrical details must be checked for your hardware.

## Tests

Offline tests do not import server.py or call real models/cloud APIs. C tests execute extracted firmware functions with a fake SDK through clang and llvmlite. Install requirements-dev.txt only if you want these development tests. Make clang (LLVM compatible with llvmlite 0.47) available in PATH or set DEXTER_ESP_CLANG to its executable. The test fixture can also use DEXTER_ESP_SOURCE for another source tree.

```powershell
python -B -m unittest -q test_command_states test_session_runtime test_esp_transport test_reconnect_timeouts test_playback_ack test_cancel test_pcm_edges test_local_config test_tts_upgrade test_llm_stream test_phrase_buffer test_streaming_audio test_streaming_transport test_streaming_mode test_length_handling
```

Tests needing clang/llvmlite require both tools; a missing dependency is not a passing firmware test. 224 tests cover states/framing/timeouts, streaming phrase order and backpressure, ACK/CANCEL, PCM/long text, configuration, voice switching, phonetic normalization, prompt/history and partial-length outcomes. See STATUS.md/PUBLICATION.md for actual results.

## Evidence and limits

PROJECT.md describes scope; ROADMAP.md planned work; ARCHITECTURE.md implementation; STATUS.md actual checks; BASELINE.md measurements. Historical snapshot paragraphs are preserved, so newest evidence takes priority. Model timing is a small local diagnostic sample, not a latency guarantee. GPIO/device characteristics and runtime install compatibility still require per-machine verification. No automatic credential rotation was performed. Keep local backups outside the repository.

## Stable recovery point

The accepted streaming milestone is tagged `v0.3.0-streaming-stable`; the prior New TTS milestone remains at `v0.2.0-tts-stable`. To inspect/recover
it without overwriting a current worktree with local changes, use:

```powershell
git fetch origin tag v0.3.0-streaming-stable
git worktree add --detach ../dexter-v0.3.0 v0.3.0-streaming-stable
```

The new worktree still needs its own ignored `.env`, ESP local configuration and
trusted weights. Local configuration/model files are not restored by Git. Preserve
any existing settings separately; do not use reset --hard or force push to roll back.
