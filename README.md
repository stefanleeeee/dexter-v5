# AI Dexter — stable phase 1

A voice assistant using an ESP32-S3 microphone/speaker and a Python server. Vosk detects “Декстер”; Groq provides command transcription and LLM replies; local Silero synthesizes Russian speech. Internet and a Groq API key are required for STT/LLM. CosyVoice, voice barge-in, Windows actions and acoustic echo cancellation are not implemented.

## Architecture

ESP I2S microphone → PCM16LE mono 16 kHz → TCP → Python/Vosk wake word → Groq Whisper STT → Groq LLM → Silero aidar → PCM16LE mono 24 kHz → TCP → bounded ESP playback queue → I2S speaker.

TCP: 5-byte header `[type:u8][length:u32 big-endian]`; raw PCM, no WAV headers. MIC 0x02, START 0x11, PCM 0x12, EOS 0x13, DONE 0x14, CANCEL 0x15, CANCELLED 0x16. Control IDs are 32 bytes (connection UUID + counter). Update server/ESP as a matched pair. DONE follows DMA drain; cancellation clears software/DMA output before CANCELLED. See ARCHITECTURE.md.

## Python setup (Windows / PowerShell)

The recorded working environment is CPython 3.14.3, CPU-only torch 2.15.0. The exact installed dependency snapshot is requirements.txt; other platforms/version combinations are not yet validated.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set `GROQ_API_KEY`, listening `HOST` (normally `0.0.0.0`) and `PORT`. OS environment variables override .env. No python-dotenv dependency is needed. Only KEY=VALUE lines, whole-line comments, literal values, single-quoted strings or JSON double-quoted strings are supported; there is no variable interpolation or inline comment syntax. Never commit the filled file. Invalid/missing required configuration fails without printing its value. Normal logs redact configured API keys and IPv4 addresses, but recognized speech/LLM content remains private user data; inspect logs before sharing.

Place a Russian Vosk model directory at `model_ru/` and the trusted Silero package file at `v4_ru.pt`, next to server.py. Weights/audio are intentionally not included. Sources: [Vosk model catalog](https://alphacephei.com/vosk/models), [Silero models](https://github.com/snakers4/silero-models). Use the corresponding Russian Vosk model and Silero v4_ru package, respect their licenses, and compare model hashes with your own trusted source. Exact model tree/weight hashes from the tested baseline are in BASELINE.md. Loading a torch package executes package code: only use trusted weights.

Start from the repository root:

```powershell
.\.venv\Scripts\python.exe -B server.py
```

The server listens on the configured port. Configure firewall/LAN access for the ESP. TCP is plain, unauthenticated LAN transport; do not expose it directly to the public internet. Type `cancel` or `отмена` + Enter in the server console to cancel the current response. In-flight model calls finish naturally; their canceled results are not sent. No microphone voice interruption is implemented.

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
python -B -m unittest -q test_command_states test_session_runtime test_esp_transport test_reconnect_timeouts test_playback_ack test_cancel test_pcm_edges test_local_config
```

Tests needing clang/llvmlite require both tools; a missing dependency is not a passing firmware test. The original 136 regression tests cover states, framing, timeouts, ACK/CANCEL, PCM and long text; configuration tests are added for this release. See STATUS.md/PUBLICATION.md for actual results.

## Evidence and limits

PROJECT.md describes scope; ROADMAP.md planned work; ARCHITECTURE.md implementation; STATUS.md actual checks; BASELINE.md measurements. Historical snapshot paragraphs are preserved, so newest evidence takes priority. Model timing is a small local diagnostic sample, not a latency guarantee. GPIO/device characteristics and runtime install compatibility still require per-machine verification. No automatic credential rotation was performed. Keep local backups outside the repository.
