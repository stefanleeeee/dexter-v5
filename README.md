# AI Dexter — Phase 4 Contextual Voice Dialogue

A voice assistant using an ESP32-S3 microphone/speaker and a Python server. Vosk detects “Декстер”; Groq provides command transcription and LLM replies; local Silero V5.6 synthesizes Russian speech on CPU. Phases 1–4 are complete and accepted. RAM dialogue memory and safe wake-word-free follow-up are implemented. Phase 5 Windows control is next, not implemented. Internet and a Groq API key are required for STT/LLM. CosyVoice, voice barge-in, Windows actions and acoustic echo cancellation are not implemented.

## Architecture

ESP I2S microphone → PCM16LE mono 16 kHz → TCP → Python/Vosk wake word → Groq Whisper STT → Groq LLM streaming → completed-phrase buffer → phonetic normalization → Silero V5.6 CPU (selectable voice) → PCM16LE mono 24 kHz → TCP → bounded ESP playback queue → I2S speaker.

TCP: 5-byte header `[type:u8][length:u32 big-endian]`; raw PCM, no WAV headers. MIC 0x02, START 0x11, PCM 0x12, EOS 0x13, DONE 0x14, CANCEL 0x15, CANCELLED 0x16; follow-up probe/support 0x17/0x18 and fresh capture request/ready 0x19/0x1A. Control IDs are 32 bytes (connection UUID + counter). Update server/ESP as a matched pair. DONE follows DMA drain; cancellation clears software/DMA output before CANCELLED. See ARCHITECTURE.md.

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

## Этап 4 завершён и принят пользователем

Пользователь подтвердил аппаратную приёмку памяти, компактных записей, политики
длины и безопасного follow-up. В последнем тесте зафиксированы FRESH CAPTURE
REQUESTED/CONFIRMED, FOLLOWUP WINDOW OPENED/ACCEPTED, успешные уточнения без имени,
PLAYBACK DONE, WINDOW EXPIRED и ограничение истории шестью парами.
Это подтверждение владельца, не новый аппаратный тест при публикации. Дополнительные
Reset/акустические/длительные сценарии не объявляются проверенными без отдельных
данных. Следующий этап 5 — управление Windows; он не реализован и не начат.
Эта запись приоритетнее исторических pending/blocked ниже.

## Краткосрочная память диалога — этап 4.1

Память временная, одна на логическую однопользовательскую беседу в процессе.
Переподключение ESP32 сохраняет её, перезапуск сервера очищает. Это не постоянное
хранилище персональных данных: файлов, базы данных и суммаризации нет.
Сохраняются исходный текст Whisper и видимый ответ LLM, без фонетической замены,
reasoning, аудио или метрик. System prompt остаётся отдельно.

| Переменная | По умолчанию | Допустимые значения |
| --- | --- | --- |
| `DIALOG_MEMORY_ENABLED` | `true` | `true`, `false`, `1`, `0` |
| `DIALOG_MEMORY_MAX_TURNS` | `6` | целое от 1 до 100, пар user + assistant |
| `DIALOG_MEMORY_MAX_TOKENS` | `1500` | целое от 1 до 32768, оценка только истории |

Настройки читаются при запуске через прежний механизм `.env`/переменные среды;
среда имеет приоритет. Например:

```powershell
$env:DIALOG_MEMORY_ENABLED = "true"
$env:DIALOG_MEMORY_MAX_TURNS = "6"
$env:DIALOG_MEMORY_MAX_TOKENS = "1500"
.\.venv\Scripts\python.exe server.py
```

Для постоянных локальных настроек добавьте эти строки в игнорируемый `.env`.
Изменения требуют перезапуска. Увеличение истории увеличивает входные токены Groq.
1500 — ориентировочный бюджет, не гарантия точного расхода: локальная эвристика
считает примерно один токен на два кириллических или три ASCII-символа, остальные
символы — по UTF-8 байтам, плюс 16 на сообщение, с округлением вверх. Это оценка
с запасом для обычной речи, не точный токенизатор: она может завышать и занижать
реальные токены. Установленные библиотеки tokenizers/transformers сами по себе
не дают проверенного токенизатора GPT-OSS; совместимый локальный набор не найден,
загрузка/установка не выполнялась. Usage завершённого запроса не используется
как стоимость пары истории: оно включает другое содержимое и reasoning.

После исправления 4.1.1 различаются:

- **Полный ответ** — без изменений выводится и озвучивается. Если пара помещается
  в бюджет, в памяти остаются исходные вопрос и ответ целиком.
- **Компактная запись** — если полная пара не помещается, вопрос сохраняется
  точно, ответ представлен исходными предложениями с начала и, если помещается,
  заключительным предложением. Маркер прямо сообщает модели о сокращении и
  пропусках. Это детерминированная выборка, а не смысловая суммаризация:
  факты не создаются, детали из середины могут теряться. Обычная компактная пара
  занимает до 512 оценочных токенов; длинное первое целое предложение может
  использовать больше, в пределах общего лимита истории. Новых настроек нет.
  Если даже первое предложение не помещается или нет надёжной границы,
  сохраняются исходный вопрос и пометка, что подробности ответа не сохранены.
- **Вытесненная пара** — ранее сохранённая пара целиком удалена как самая старая
  при превышении числа пар, суммы оценок или строгого контекстного ограничения.
  Полная/компактная пара не разрывается посередине.
- **Пропущенная пара** — даже вопрос с минимальной пометкой не помещается;
  новая пара не сохраняется, прежние пары только из-за этого не стираются.

Сокращение применяется после ACK только к записи RAM; текущий LLM-запрос,
полный ответ в консоли и текст для Silero не переписываются. Дополнительных API
запросов, моделей или файлов истории нет. Стартовые настройки 6/1500 сохранены.

Дополнительно учитываются system prompt, текущая команда и резерв
`LLM_MAX_COMPLETION_TOKENS` внутри [контекста модели 131072 токена](https://console.groq.com/docs/model/openai/gpt-oss-120b),
с запасом 1024 токена на форматирование. Здесь отдельно сохранена прежняя строгая
проверка по UTF-8 байтам плюс 16 на сообщение для **всего** отправляемого контекста;
более умеренная эвристика истории её не заменяет и защиту окна не ослабляет.
Текущая команда не обрезается: если
даже она с system prompt не помещается по консервативной оценке, запрос явно
отклоняется до обращения к LLM.

Пара сохраняется только после полного ответа (`stop` / `done`) и успешного
matching playback ACK. `length`, `empty_length`, отмены, пустые ответы и ошибки
не пополняют память. Отмена/разрыв после уже полученного ACK не отменяет факт
успешно завершённой реплики; при разрыве до записи действует session guard.
История не передаётся из старого worker в новое TCP-соединение напрямую:
общий pipeline сериализован, устаревшие результаты отклоняются.

Локальные команды в той же консоли сервера:

```text
memory status
memory clear
```

`status` показывает включение памяти, число/лимит пар, полные (`memory_full_turns`)
и компактные (`memory_compact_turns`) пары, оценку/лимит токенов, вытеснения и
пропуски (`memory_skipped_turns`). `memory_last_compacted` показывает сокращение
последней записанной пары; `memory_last_store` различает `stored_full`,
`stored_compact`, `skipped_budget`, `cleared`, `initial`. Содержимое не выводится.
`clear` сообщает число удалённых пар,
не сбрасывает модели или TCP. Даже если очистить память во время STT/LLM/TTS/
ожидания ACK, завершение прежней команды не восстановит её в истории.
Уже отправленный в Groq контекст отозвать нельзя; текущий ответ не прерывается.
Для прерывания остаётся `cancel`. Голосовой очистки памяти нет.

`[MEMORY METRICS]` содержит `memory_enabled`, `memory_turns`, `memory_tokens_est`,
`memory_evicted_turns` (накопительный счётчик), `memory_context_tokens_est`
(system + история + текущая команда, без completion и резерва), а также
`memory_context_tokens_bound` — прежний строгий байтовый бюджет контекста.
После успешного завершения `[MEMORY STATUS]` показывает состояние после записи.
После clear число полных/компактных пар и оценка равны нулю, last_compacted=false;
накопительные счётчики вытеснений и пропусков сохраняются до перезапуска Python.
Полная история автоматически не печатается. Прежние логи текущего вопроса и
ответа сохранены; `memory clear` не удаляет текст из консольного scrollback.


## Этапы 4.2 и 4.3 — длина ответов и безопасное продолжение

Политика длины4.2 принята пользователем: факт1–2 предложения, вопрос2–4,
объяснение3–6; явные «подробнее», «короче», «продолжай» и число предложений
учитываются. Это инструкции, не обрезание готового ответа. 2048 completion tokens
не менялись. Гарантии абсолютной точности нет. Пустой STT не вызывает LLM/TTS.

Follow-up4.3 реализован для новой прошивки и принят пользователем после аппаратной проверки.
Память4.1.1 не менялась. Никаких дополнительных STT/LLM запросов на команду нет.

| Переменная | Default | Значения |
| --- | --- | --- |
| DIALOG_FOLLOWUP_ENABLED | true | true/false/1/0 |
| DIALOG_FOLLOWUP_TIMEOUT_S | 12 | конечное число1..60 секунд |

Настройки читаются при запуске, среда имеет приоритет над.env. Для отключения:

```powershell
$env:DIALOG_FOLLOWUP_ENABLED = "false"
.\.venv\Scripts\python.exe server.py
```

С false нет новых управляющих кадров или автоматического окна; каждое обращение
требует имени, как до4.3. Политика краткости и память остаются. При true startup
status сначала supported=false,active=false,reason=awaiting_firmware_capability:
это начальное состояние до проверки соединения, а не прежняя постоянная блокировка.
После проверки появляются FOLLOWUP SUPPORTED и FRESH CAPTURE CONFIRMED.

### Свежий микрофон и протокол

Wake word, VAD и приём команды остаются на Python; ESP передаёт микрофон постоянно.
Формат кадров прежний: type:u8 + length:u32BE + body. PCM и START/EOS/ACK/CANCEL
не менялись. Добавлены свободные типы:

| Тип | Направление | Тело | Значение |
| --- | --- | --- | --- |
| 0x17 | Python→ESP | 0 байт | Запрос поддержки fresh capture v1, один раз на соединение при первой команде |
| 0x18 | ESP→Python | 0 байт | Поддержка v1; только в ответ на probe |
| 0x19 | Python→ESP | 32 байта | Запрос свежего mic после normal DONE; тот же полный UUID/counter ID ответа |
| 0x1A | ESP→Python | те же32 байта | READY: старый mic исключён, свежий uplink можно использовать |

Старая проверенная прошивка игнорирует неизвестный control с нулевым телом.
Без0x18 новый сервер не отправляет0x19 и остаётся wake-word-only. Новая прошивка
не шлёт capability сама, поэтому со старым сервером сохраняет обычный режим.
READY принимается только текущим socket reader для ожидаемого полного ID;
устаревший, повторный вне ожидания или ID старого соединения не открывает окно.

ESP по запросу инвалидирует mic generation, очищает ring, временно запрещает
capture enqueue. Задача микрофона (не TCP) ждёт300ms акустического хвоста,
делает RX disable/enable и считывает с отбрасыванием больше полной RX DMA ёмкости.
В используемом ESP-IDF6.1 disable сбрасывает current DMA pointer/rw_pos, enable
очищает RX message queue; дополнительное вычитывание исключает остатки DMA.
Старый read/producer generation не может снова положить PCM в очередь.
Driver calls не выполняются под mic mutex. Только после успешного READY send
TCP разрешает enqueue; все MIC кадры после READY относятся к свежему захвату.
300ms не является доказательством свежести: его дают generation+queue+RX barrier.

Producer drain ограничен1s после паузы; ESP request deadline2s, Python wait2.5s.
Ошибка reset/read или deadline не выдаёт READY. Ошибка RX reset/read sticky:
нужен Reset ESP, повторное TCP-подключение само её не лечит. Ошибка/частичная
отправка управляющего кадра приводит к обычному закрытию TCP. Без READY окна нет.

### Окно и ограничения

Только stop/done + matching playback ACK разрешает запрос свежего микрофона.
Partial length,empty,cancel,error окна не открывают. После READY и освобождения
worker/writer/pipeline receiver переводит существующий mode в FOLLOWUP.
С этого момента monotonic deadline = now+12s. Шум/тишина его не продлевают.
На третьем подряд VAD-speech кадре (90ms) начинается один COMMAND с этими кадрами;
для отправки в Whisper нужно суммарно не менее300ms VAD speech. Далее сохраняются
прежние пределы COMMAND: конец по тишине1.53s, максимум около7s. Короткий шум
отбрасывается без STT, повторное окно после него не открывается.

Речь должна подтвердиться до deadline; после него снова нужен «Декстер».
Следующий successful done может открыть новое окно. CANCEL в окне, memory clear,
ошибка и reconnect сбрасывают eligibility; console только сигнализирует, state
меняет receiver. Во время PROCESSING/playback MIC отбрасывается. Новых параллельных
worker/reader/writer нет. Timeout окна не очищает RAM-память.

Свежесть цифровой записи не устраняет физическое эхо. AEC/barge-in отсутствуют.
Нужно дождаться FOLLOWUP WINDOW OPENED: речь сразу после звука, до fresh barrier,
может потерять начало. Очень короткие ответы менее300ms могут быть отброшены.
Длительная музыка/чужая речь может пройти VAD; это не распознавание владельца.
Пауза300ms требует проверки в вашей комнате, не гарантирует подавления любой
реверберации. Значения I2S, PCM, DMA и размер очереди не увеличивались.


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
python -B -m unittest discover -p 'test_*.py' -q
```

Tests needing clang/llvmlite require both tools; a missing dependency is not a passing firmware test. 304 tests cover states/framing/timeouts, streaming phrase order and backpressure, ACK/CANCEL, PCM/long text, configuration, voice switching, phonetic normalization, prompt/history and partial-length outcomes. See STATUS.md/PUBLICATION.md for actual results.

## Evidence and limits

PROJECT.md describes scope; ROADMAP.md planned work; ARCHITECTURE.md implementation; STATUS.md actual checks; BASELINE.md measurements. Historical snapshot paragraphs are preserved, so newest evidence takes priority. Model timing is a small local diagnostic sample, not a latency guarantee. GPIO/device characteristics and runtime install compatibility still require per-machine verification. No automatic credential rotation was performed. Keep local backups outside the repository.

## Stable recovery point

The accepted dialogue milestone is tagged `v0.4.0-dialogue-stable`; the previous streaming and New TTS tags remain available. To inspect/recover
it without overwriting a current worktree with local changes, use:

```powershell
git fetch origin tag v0.4.0-dialogue-stable
git worktree add --detach ../dexter-v0.4.0 v0.4.0-dialogue-stable
```

The new worktree still needs its own ignored `.env`, ESP local configuration and
trusted weights. Local configuration/model files are not restored by Git. Preserve
any existing settings separately; do not use reset --hard or force push to roll back.
