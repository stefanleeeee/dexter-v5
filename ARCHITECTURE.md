# Фактическая архитектура Dexter

## Этап 4 завершён и принят пользователем

Пользователь подтвердил аппаратную приёмку памяти, компактных записей, политики
длины и безопасного follow-up. В последнем тесте зафиксированы FRESH CAPTURE
REQUESTED/CONFIRMED, FOLLOWUP WINDOW OPENED/ACCEPTED, успешные уточнения без имени,
PLAYBACK DONE, WINDOW EXPIRED и ограничение истории шестью парами.
Это подтверждение владельца, не новый аппаратный тест при публикации. Дополнительные
Reset/акустические/длительные сценарии не объявляются проверенными без отдельных
данных. Следующий этап 5 — управление Windows; он не реализован и не начат.
Эта запись приоритетнее исторических pending/blocked ниже.

## RAM memory and dialogue policy

A single process-local DialogMemory deque belongs to the conversation, not TCP.
Full original user/visible assistant pairs are saved after stop/done and matching
playback ACK; partial length/empty/cancel/error/stale results are excluded. Clear
and commit use a version and mutex, with CommandSession guards intact. Reconnect
retains completed pairs; process restart clears them. No disk history/reasoning/audio.
History limits default6 turns/1500 estimated tokens. Whole old pairs are evicted;
oversized answers become explicitly marked original-sentence extracts, retaining
the exact question; if even the minimum cannot fit, the pair is skipped. Token
estimate is approximate (ASCII/3,Cyrillic/2,other UTF8 bytes,+16/message); total
model-window protection separately retains the strict byte+16 budget including
system/current/history,completion reserve and1024 overhead. No summarization API.
System prompt guides proportional short answers, explicit detail and uncertainty;
model/budget/phonetic rules and both streaming/full paths remain unchanged.

## Этап4.3 — fresh-capture follow-up, принят пользователем

Актуальный приоритет над историей ниже:политика4.2 принята пользователем,
follow-up4.3 реализован с разрешённым согласованным расширением обеих сторон.
0x17/0x18 zero-body probe/support;0x19/0x1A full32byte responseID request/ready.
Существующие headers,PCM,0x11..0x16,модели,память иstreaming не менялись.
Пока capability/READY отсутствуют — wake-only fallback. СтарыйESP безопасно
игнорирует0x17,новыйESP не отвечает безprobe,новыйсервер не шлётbody вlegacy.

Probe/fresh пишет существующий command worker; ACK/capability/READY читает
единственный receiver. ESP freshrequest привязан к последнему фактически
отправленному normalDONE,одноразовый fullID включаетUUIDconnection/counter.
Micmutex вращаетgeneration,очищаетring,блокируетenqueue.DRAIN исполняетmic task:
300ms acousticpause, RXdisable/enable,discard >реальнойtotalDMAbytes с1sdeadline,
20ms/read. ESP-IDF6.1 RXqueue/currentpointer reset проверен влокальномSDK.
Drivercallsвнеmutex/TCP. READY отправляет толькоTCP передreleaseenqueue.
Старый producerread/generation не можетenqueue послеfence. ОшибкаRX sticky доReset;
ESPdeadline2s/Pythonwait2.5s;нетREADY→нетокна,partialsend→disconnect.

Receiver mode расширен FOLLOWUP внутри CommandSession, второйstate machine нет.
PROCESSING включаетplayback иfreshwait. Непустойstop/done+ACK разрешаетfreshrequest;
partial/cancel/error/empty не разрешают. Poll открываетwindow толькопослеREADY
иосвобожденияworker/writer/pipeline. Deadline monotonic12s не продлевается шумом.
3VADframes подтверждаютначалоCOMMAND,не меньше10VADframes/300ms речи доSTT;
остальные COMMAND границы прежние. Console cancel/clear толькосигнализируют,
receiverсбрасываетmode. Reconnectне переноситwindow;timeoutне очищаетRAMmemory.

Самопрослушивание старойзаписи исключается fence+READYordering,неоднимsleep.
Физическоеэхо/музыка неустранены:нетAEC/voicebarge-in.Пауза300ms доRXreset требует
аппаратнойпроверки;ранняя речь можетбытьотброшена. Подробностипротокола,tests,
flash/rollback — FOLLOWUP_PLAN.md/README.md.304tests/fullbuildpassed,плату прошил и проверил пользователь; агент её не прошивал.


## Этап 3 — принятая streaming-архитектура (2026-11-06)

Groq LLM streaming → буфер законченных фраз → фонетическая нормализация → Silero
V5.6 CPU → PCM16LE mono24000Hz → TCP → ESP32 → I2S. Streaming читает deltas
постепенно; Silero синтезирует фразы последовательно целиком, без native TTS
stream API. Это не двусторонний speech-to-speech, voice barge-in или AEC.

CommandSession по-прежнему владеет поколением/socket: один worker writer иодин
reader, общий pipeline/model lock. Памятьограничена: rawtext32768chars,
phrasebuffer900chars, однаaudioфразавработе; slow sendall backpressures next TTS/
HTTPread, no unbounded queue. Speaker snapshot наответ, modelCPU/4threads/24kHz.
Один STARTпослепервогоготовогоPCM, последовательныеevenchunks<=4096, одинEOS/
matchingACKпослеfinaltail. HTTP-onlywatcherbest-effortзакрываетstreamприcancel;
начатыйTTS/открывающийсяSDKrequestне убиваются. ExistingCANCELLED/deadlines/
disconnectfallbackсохранены, stalePCMне переходитвновуюgeneration.

Length — partial result: tailбезавтодополненияозвучивается,EOS/ACKбезcancelтолько
из-заlength, partialнеhistory-complete. Emptylengthне воспроизводится. Stopdone,
manual/stalecancelиactualerrorsразличаются. Visiblecontentотдельноотnumericmetrics,
reasoningfieldsне озвучиваются/не логируются, usageотсутствует→null.
Defaultstreamtrue/config2048/low/include_reasoningfalse; false—fallbackполного
ответа. Envприоритетнее.env,startupread→restart. ЧетырепараметраразобранывREADME.

Этапы1–3 принятывладельцем; приёмка110sответа подтвержденасоstop/done/ACK. Сведения
о другихсценариях ниже исторические,не превращатьихвновыеhardwareclaims.
Этап4 — контекст/естественноеобщение,не реализован. Firmware/протоколвэтомрелизе
не меняются. Подробности/метрики/границы STREAMING.md/STATUS.md.


## Accepted Phase 2 — Silero V5.6

The active default TTS is Silero v5_6_ru, CPU/4 threads, raw PCM16LE mono24000Hz.
VoiceSettings snapshots one speaker for a complete TTS reply. The existing console
reader handles voice list/status/name and cancel. The stdlib-only phonetic
normalizer transforms a TTS copy, preserving original LLM history and protected
literals. Known names get reviewed Cyrillic spellings; unknown Latin is retained.
Groq's system prompt has the same scope. Local config selects model/voice and
normalizer flag. Single socket reader/writer, ACK/CANCEL, playback IDs, pipeline
lock and ESP firmware remain as in phase1. LLM/TTS streaming is not implemented;
it is planned for phase3. The owner has accepted phase2; older status snapshots
below are historical. See TTS_SETTINGS.md and STATUS.md.

## Уточнение baseline 1.9 — 2026-11-06

Рабочий тракт не менялся. .venv 3.14.3/Vosk/Silero проверены локальным smoke, PATH Python отличается; запускать явно .venv из корня проекта. Torch CPU-only; текущие hardware/version/hash/RTF и пределы доказательств — BASELINE.md и <private baseline JSON; intentionally excluded>. pressure считает повторные неудачные enqueue с сохранением PCM, mic_drop — failed mic enqueue при connected generation, не намеренный disconnected discard. Пользователь подтвердил короткий DONE/длинный CANCELLED 1.8, не полный длинный ответ; Reset при cancel/сквозные замеры остаются открыты. Новые документы имеют приоритет над историческими статусами ниже, параметры моделей/PCM/ACK не менялись.


## Уточнение 1.8 — 2026-11-06

Приоритет над историей ниже: VAD/AGC считают abs PCM16 в int32; TTS mono проверяется на непустой конечный 1D массив, ограничивается [-1,1] и сериализуется как PCM16LE. Длинное предложение делится на вызовы Silero <=900 символов по пробелам с сохранением остатка, без молчаливого усечения. Пустой текст — no-op без START. Нечётный mic payload отклоняется закрытием; пустой/нечётный command PCM отвергается до WAV с обычным finally cleanup. Частоты, размеры буферов, модели, ACK/CANCEL/reconnect и прошивка не менялись. 136 тестов успешны; реальный синтез/прослушивание нового дробления не проверены. Пользователь подтвердил две аппаратные отмены 1.7 и следующую команду, но не Reset при отмене. Детали — STATUS.md.


## Актуальная архитектура 1.7 — 2026-11-06

Этот раздел имеет приоритет над историей ниже. Реализована локальная отмена (`cancel`/`отмена` + Enter, cancel_control.py), без голосового barge-in. CommandSession разделяет disconnect и кооперативную отмену команды; один worker пишет целые кадры, единственный socket reader принимает ACK. Уже начатые вызовы моделей не прерываются принудительно, поздние результаты подавляются; общая блокировка pipeline сохранена.

Добавлены CANCEL 0x15 и CANCELLED 0x16 с тем же 32-байтным UUID-соединения/counter ID, что START 0x11 / EOS 0x13 / DONE 0x14. Заголовок, PCM16LE mono 16 kHz uplink / 24 kHz downlink и размеры буферов прежние. После принятой отмены normal DONE не завершает ответ; старый ID/соединение и повторный START не оживляют звук. Framed START требует возрастающий counter и тот же UUID; после отмены PCM без нового START отвергается. До первого framed START legacy режим сохранён.

TCP отменяет pending staging; playback под mutex меняет generation и очищает ringbuffer. Единственный consumer после возврата текущего ограниченного write делает disable → preload нулями всей существующей DMA → enable (FIFO reset драйвером), затем свежий zero-output EOF barrier. Только после этого TCP writer посылает CANCELLED. Driver calls выполняются вне mutex, scratch используется повторно, параметры I2S/queue не увеличены. Disconnect также запрашивает очистку. Reset API failure фиксирует sticky error без success ACK, требует Reset платы.

Python watchdog/ожидание отмены 2 s закрывает неподтверждённое соединение abortive TCP fallback. Это не success и не гарантия акустической задержки: CANCEL следует за уже буферизованным PCM, удалённая остановка при обрыве зависит от его обнаружения. Новый сервер и прошивку обновлять вместе для CANCELLED; старый ESP не подтверждает отмену. История, уже сохранённая до TTS, не откатывается. 124 offline теста/полная сборка успешны; аппаратная отмена не проверена. 1.6 подтверждена пользователем в указанном в STATUS охвате, полный синхронный Reset/DMA остаётся открытым. Детали, откат и сценарий приёмки — STATUS.md. 1.8 не начата.


## Изменения 1.6 — 2026-11-06

Текущее расширение: START 0x11 и STOP/EOS 0x13 несут payload 32 байта (16 байт UUID соединения Python + 16 байт счётчика ответа big-endian). PCM 0x12/24 kHz и MIC 0x02/16 kHz остаются прежними. ESP возвращает новый success ACK 0x14 с теми же 32 байтами; header всё ещё type:u8 + length:u32 big-endian. Обновить Python и ESP вместе: старый ESP не поддерживает START/STOP с телом и ACK. Новая ESP принимает legacy START/STOP нулевой длины без гарантии окончания/ACK; новый Python не делает fallback на legacy send-complete.

send_audio_to_esp32 изменён только по транспорту: begin_playback → прежний синтезированный PCM → настоящий EOS → ожидание ACK. Основной reader dispatch ACK независимо от PROCESSING и продолжает MIC; worker не читает сокет. В START/EOS сопоставляется полный токен; ранний/stale/wrong-ID ACK не завершает ответ. ACK-wait 10 s, disconnect/timeout инвалидируют соединение и будят worker с ошибкой. WAKE_WORD возвращается лишь по завершению worker после ACK, не после отправки PCM.

На ESP consumer ставит DMA-барьер после software queue и **последнего завершённого I2S write**. Snapshot счётчика ISR on_sent + `2*ceil(total_dma_buf_size/event.size)+2` новых EOF подтверждают прохождение DMA с запасом нулевого вывода; старые EOF не учитываются. DMA capacity читается из канала, размеры/частоты/GPIO/очередь не увеличивались. Единственная необходимая TX-настройка — auto_clear_after_cb=true: без очистки circular descriptors могут повторять старый PCM, в том числе у короткого допустимого ответа без TTS-хвоста. Основание проверено по установленному i2s_common.c и [официальному ESP-IDF API](http<local-path>). on_sent callback зарегистрирован до enable, ISR трогает лишь короткую portMUX-защищённую статистику.

Consumer никогда не пишет TCP: ACK-ready забирает TCP task, пишет целый ACK между mic-фреймами. EOS/error/drain reset защищены поколением. Нет ACK при I2S error/нет callbacks; EOS deadline 10 s завершает соединение. Подтверждение — на уровне цифрового I2S/DMA, не акустический датчик динамика. Привязка к реальному слышимому концу требует аппаратной проверки; 91 тест и полная сборка прошли, 1.6 ещё не прошивалась агентом. Прерывание 1.7 не добавлялось. Точная спецификация и сценарии — STATUS.md; описания START/STOP без действия ниже исторические.

## Изменения 1.5 — 2026-11-06

В `main/playback.c/.h` выделен единственный I2S consumer, подключённый через main/CMakeLists.txt. BYTEBUF playback-очередь ограничена 16384 байтами; TCP читает PCM-body неблокирующими шагами в staging 2048 байт, между шагами продолжает mic uplink. Полный queue не теряет PCM: staging сохраняется, recv приостанавливается до свободного места или общего 10 s deadline. DATA до 16384 байт всё ещё допустима, разбивка по 2048 — только внутренняя. Per-packet malloc удалён. I2S consumer копирует <=2048 байт в scratch и возвращает BYTEBUF item под mutex до driver call, соблюдая правило ESP-IDF «один outstanding receive». Другой task не ждёт I2S под этим mutex.

I2S write: 20 ms на вызов, бюджет блока 250 ms плюс погрешность последнего вызова/scheduling; partial ESP_ERR_TIMEOUT учитывает реально принятые байты. При отказе consumer выставляет generation-guarded failure, TCP закрывает соединение штатным путём. Disconnect очищает ожидающую очередь и сменяет generation; ошибка старого consumer не действует на новую сессию. Уже принятый I2S/DMA PCM не отзывается. Пустой consumer уступает CPU на 10 ms и сразу возобновляет вывод при новых данных; подтверждённого EOS/ACK нет. Счётчик empty включает конец ответа и не является точной мерой underrun.

Раз в 5 s `[AUDIO]` сообщает queue/high, backpressure, empty, i2s_bytes, errors, mic_drop, heap. queue/high <=16384; i2s_bytes означает принятые драйвером байты, не факт окончания звука. Очередь добавляет до ~341 ms программного хвоста плюс I2S/DMA; send-complete всё ещё не playback-complete (1.6). Сервер/модели/PCM/частоты/GPIO/I2S init не изменены, новых TCP-сообщений нет. 71 тест и полная сборка прошли, аппаратная 1.5 ожидается. Пользователь подтвердил 1.3/1.4 в описанном в STATUS охвате; Reset-ошибки не доказывают самопроизвольный обрыв. Исходное описание monolithic TCP/I2S ниже историческое. 1.6 не выполнялась.

## Изменения 1.4 — 2026-11-06

TCP task единолично владеет локальным socket fd. Mic task получает generation под mutex до I2S read и может enqueue только в то же активное поколение. На disconnect/connect программная очередь очищается под тем же mutex, старый in-flight PCM отвергается. WIFI_CONNECTED_BIT сбрасывается при Wi-Fi disconnect; TCP connected публикуется лишь после успешного connect, socket options и проверки Wi-Fi. Ожидание AP не создаёт socket, ошибки/обрывы ведут к закрытию и retry через 2 s.

Connect через nonblocking/select/SO_ERROR ограничен 10 s. Начатый downlink-header и DATA-body имеют отдельные monotonic deadlines 10 s; idle downlink разрешён на неограниченное время, поскольку сервер может долго выполнять STT/LLM/TTS. Mic header+body отправляются MSG_DONTWAIT с общим сроком 10 s, EAGAIN повторяется с паузой, при частичном downlink-header срок отправки дополнительно ограничен его deadline. Body recv остаётся с per-call SO_RCVTIMEO 50 ms: возможна эта добавка и scheduler jitter к обработке истечения срока. Python recvall через select имеет общий срок 10 s на вызов, без переключения shared socket timeout; Python sendall уже имел timeout 10 s на вызов. Предела для всего голосового ответа не добавлено.

main/CMakeLists.txt явно подключает установленный компонент ESP-IDF esp_timer, новые библиотеки не устанавливались. Протокол/PCM/модели и I2S init неизменны. 57 тестов и полная ESP32-S3 сборка прошли; аппаратные 1.3/1.4 остаются неподтверждёнными. Блокирующий I2S и неотменяемые начатые SDK/model вызовы не получили deadline этой задачей; аппаратные DMA/RX FIFO/TX playback не сбрасываются. Старые описания отсутствующих сроков/очистки ниже относятся к прошлым снимкам. Детальные проверки и границы — STATUS.md. 1.5 не начата.

## Изменения 1.3 — 2026-11-06

В активном `main/hello_world_main.c` добавлены tcp_send_all/tcp_send_mic/tcp_play_audio/tcp_handle_message. Частичные TCP отправки header/body добираются, EINTR повторяется; send failure завершает соединение. PCM принимается только чётной длины <=16384; пустая DATA — no-op. malloc/recv/I2S failure возвращают ошибку в TCP task, который закрывает соединение вместо разбора остатка тела как заголовка. Успешные частичные I2S записи добираются, нулевой прогресс/некорректный bytes_written не игнорируются; буфер освобождается. Неожиданный ненулевой payload других типов отвергается закрытием, нулевые START/STOP по-прежнему не управляют playback. Типы, endian, частоты, GPIO и аудиобуферы неизменны. Обработка P3 и соответствующей части P5 проверена 13 C fault-injection тестами; вместе с регрессиями прошли 33 теста, полная ESP-IDF сборка успешна. На плате 1.3 ещё не проверена. Тайм-ауты, mic queue reconnect и разнесение задач из 1.4/1.5 не менялись. По 1.2 подтверждены пользователем обычный цикл/reconnect, но не специальный restart во время длинного ответа. Подробности — STATUS.md; исходные проблемные описания ниже исторические.

## Изменения после исходного обследования — 2026-11-06

Разделы ниже сохраняют снимок 2026-11-05; этот раздел имеет приоритет для текущей реализации. 1.1 исправила переходы состояний и очистку хвоста payload; базовый голосовой цикл принят пользователем на ESP32. 1.2 добавила `session_runtime.py` и CommandSession: владелец сокета/поколения, один worker-писатель, событие завершения для приёмника — владельца режима, shutdown/close и отмена устаревшего результата после disconnect. Worker работает с копией state и уникальным именем WAV, общая история/Silero защищены последовательным доступом к pipeline между поколениями. Проверка активности и commit истории согласованы с disconnect; SDK/model вызов, уже начавшийся, завершается естественно, следующий worker ждёт lock. Это не playback ACK, очистка очереди ESP или голосовая отмена.

Транспортная функция TTS, recvall, протокол и модели не изменены. 20 автоматических тестов прошли; аппаратная проверка 1.2 ещё нужна. P1 исправлена; P2 и серверная часть lifecycle P7 обработаны в пределах 1.2. Проблемы прошивки, AEC, playback и остальные задачи остаются. requirements.txt уже обновлён вне этой задачи и содержит groq/torch/vosk/webrtcvad-wheels; исторический вывод о неполном списке не описывает текущий файл. Проверки нового окружения не проводились. Детали и ограничения — в STATUS.md.

Проверено по рабочим исходникам 2026-11-05. Ниже описана реализация, а не доказанная работа оборудования. План будущих изменений находится в [ROADMAP.md](ROADMAP.md).

## Состав проекта

| Часть | Источник истины | Назначение |
| --- | --- | --- |
| Python | `<project-root>\server.py` | TCP-сервер, wake word, запись, VAD, STT/LLM/TTS |
| Модели | `model_ru`, `v4_ru.pt` | Vosk и локальный Silero |
| Референс | `reference.wav`, `reference_text.txt` | Есть в корне, активным сервером не используются |
| ESP32-S3 | `esp32/\main\hello_world_main.c` | Wi-Fi, TCP-клиент, I2S RX/TX, mic ringbuffer |
| Сборка | Корневой и `main/CMakeLists.txt`, `sdkconfig` | ESP-IDF, target esp32s3, PSRAM отключена |

Python — монолит, собственных импортируемых модулей в корне нет. Backup Python и `hello *.txt` не являются активными точками входа. README ESP — шаблон Hello World. Пути Python к моделям и временным WAV относительные к cwd. Внешние импорты: numpy, torch, vosk, groq, webrtcvad; последние четыре отсутствуют в текущем requirements.txt. Воспроизводимость окружения не подтверждена.

## Поток данных

```mermaid
flowchart LR
  Mic[Микрофон I2S 16 kHz] --> ESP[ESP32-S3 PCM16]
  ESP -->|TCP 0x02| Py[Python: кадры 30 ms]
  Py --> Vosk[Vosk: имя Декстер]
  Vosk --> Cmd[VAD и буфер команды]
  Cmd --> WAV[AGC и WAV 16 kHz]
  WAV --> STT[Groq Whisper]
  STT --> LLM[Groq LLM]
  LLM --> TTS[Silero: полный ответ]
  TTS -->|TCP 0x12 PCM16 24 kHz| TX[ESP32 I2S TX]
  TX --> Speaker[Динамик]
```

`start_server` принимает один активный клиент, читает микрофон через `recvall`, формирует кадры 960 байт. Vosk `KaldiRecognizer(...,16000)` проверяет final/partial на «декстер»/«dexter». WebRTC VAD mode 3 вызывается при пике >1000. После имени запись идёт без pre-roll. Задуманная машина состояний: WAKE_WORD → COMMAND → PROCESSING → WAKE_WORD.

Условие конца команды: более 50 кадров тишины (51 × 30 ms = 1.53 s) либо более 233 кадров команды (234 × 30 ms = 7.02 s). Минимум перед переходом в PROCESSING — более 16000 байт, то есть 0.5 s. Эти значения описывают код, а не подобранные по тестам параметры.

`process_command` запускается daemon-потоком: нормализация к пику 28000, временный `upload_temp_<session_id>.wav` mono PCM16 16 kHz, Groq `whisper-large-v3`, затем `openai/gpt-oss-120b` (temperature 0.7, max_tokens 500). Оба облачных вызова без streaming. История глобальная, неограниченная; в prompt идут последние четыре сообщения. System prompt запрещает Markdown и просит числа словами.

Silero загружается на CPU, torch threads=4; голос `aidar`, выход 24000 Hz. Предложения длиннее 900 символов обрезаются. Все предложения синтезируются и склеиваются перед отправкой, float умножается на 32767 и приводится к native int16 без clipping. В конце 12000 нулевых байт (250 ms). `finally` обработчика удаляет временный файл и возвращает WAKE_WORD независимо от фактического окончания звука.

## Форматы, буферы и воспроизведение

| Участок | Параметры |
| --- | --- |
| I2S RX | I2S1, master Philips, 16000 Hz, mono 32 bit, LEFT; BCLK4, WS5, DIN6 |
| Преобразование RX | До 256 int32 samples (1024 байта), деление на 65536 → 256 int16 (512 байт, 16 ms) |
| Mic queue | BYTEBUF 16384 байта, теоретически 512 ms PCM; запись ждёт 10 ms, при заполнении packet drop |
| Python frame | 960 байт, 480 samples, 30 ms; буферы stream и command динамические |
| TTS network | До 4096 байт в блоке = 85.33 ms; sleep 75 ms после каждого блока |
| I2S TX | I2S0, master Philips, 24000 Hz, mono 16 bit; BCLK16, WS17, DOUT18 |
| RTOS | mic task stack 4096, priority 6; TCP task stack 4096, priority 5; tick 100 Hz |
| TCP buffers | sdkconfig: send buffer 5760, receive window 5760; это не границы протокольных сообщений |

BYTEBUF объединяет записи: один сетевой payload микрофона может быть больше 512 байт. Численные DMA-размеры не указаны в приложении, используются defaults установленного ESP-IDF; их нельзя считать равными 4096. Модель микрофона и усилителя по исходнику не установлена.

## TCP-протокол

Python слушает IPv4 `0.0.0.0:3333`; ESP подключается к настроенному в C адресу `<configured-server-ip>:3333`. Один постоянный двунаправленный сокет. Формат:

```text
offset 0:    type, uint8
offset 1..4: payload_length, uint32 big-endian
offset 5..:  payload, ровно payload_length байт
```

| Тип | Направление | Значение и реализация |
| --- | --- | --- |
| 0x02 | ESP → Python | Raw PCM16 mono 16 kHz; Python разрывает при длине >32000 |
| 0x11 | Python → ESP | START, длина 0; ESP потребляет заголовок без управления TX |
| 0x12 | Python → ESP | Raw PCM16 mono 24 kHz; ESP ограничивает длину 16384, принимает целиком, затем пишет I2S |
| 0x13 | Python → ESP | В текущем сервере только лог STOP, пакет отсутствует; активный ESP не выполняет действий по STOP |
| 0x01, 0x03 | Исторические версии | Кнопочные start/stop записи; в активной паре не используются |

PCM signed 16-bit little-endian на текущих платформах; Python использует native np.int16, что требует внимания при переносе. Нет WAV-заголовка, кодека и метаданных частоты/каналов; длины должны быть чётными. ACK, session ID на проводе, negotiation, отмена и идентификаторы чанков отсутствуют. Неизвестные типы с телом безопасно не пропускаются.

Python добирает заголовок/тело через recvall, пишет sendall; socket timeout 10 s относится и к операциям отправки. ESP имеет recv timeout 50 ms, накапливает заголовок и тело, но у тела нет суммарного deadline. Сетевой task принимает, блокирующе пишет в I2S и отправляет микрофон: естественное TCP backpressure одновременно останавливает uplink. Отдельной очереди воспроизведения и prebuffer нет; приём полного сетевого блока предшествует I2S write. START/STOP не означают физическое начало/конец звука; TX включён постоянно.

## Перепроверенные проблемы

| ID | Основание в коде | Следствие / граница доказательства |
| --- | --- | --- |
| P1 | server.py:264–273: Thread внутри условия окончания, вне проверки минимальной длины; else на уровне цепочки состояний | Предыдущий вывод «каждый кадр» неверен. Для слишком короткого завершённого буфера возможен запуск без PROCESSING; достижимость при штатных порогах не показана. Более существенный путь: оставшиеся кадры текущего stream_buffer попадают в else PROCESSING и возвращают WAKE_WORD до окончания worker |
| P2 | Общие conn/state, CHAT_HISTORY, Silero; нет lock/единственного writer; WAV назван по session_id | При пересечении обработчиков возможны перемежение TCP, гонки истории/модели/файлов. Частота возникновения не измерена |
| P3 | ESP: две send проверяют только <0 | Короткая положительная отправка теряет часть заголовка/тела; воспроизводить через fault injection |
| P4 | ESP: recv тела и i2s_channel_write в одном TCP task | Uplink ждёт; mic queue может переполниться. Потери и задержки требуют замеров |
| P5 | malloc failure не вычитывает тело; I2S result/bytes_written не проверяются | Возможна рассинхронизация и незамеченная неполная запись |
| P6 | Нет playback ACK/отмены; PROCESSING завершается после send; AEC отсутствует | Возврат к wake word может произойти до окончания звука; надёжный barge-in отсутствует |
| P7 | При reconnect queue не очищается; sock устанавливается до connect; worker не отменяется | Возможны устаревшее аудио и работа с закрытым соединением; нет управления поколением соединения |
| P8 | np.abs(int16), cast TTS без clipping; длинные фразы обрезаются | Переполнение на -32768, возможное искажение и потеря текста |
| P9 | Импорты не покрыты requirements, относительные пути, встроенные секреты | Воспроизводимость/конфигурация требуют отдельной работы; значения секретов не публиковать |

## Граница будущей интеграции

CosyVoice не установлен и не вызывается активным кодом. Предлагается адаптер TTS с выбором Silero/CosyVoice и явным возвратом к Silero; это план, не существующий API. Внешний результат для ESP остаётся mono PCM16 LE 24000 Hz. Формат выбранного runtime проверять фактически; при несовпадении нужен ресемплинг и clipping. Наличие потокового аудио у модели не заменяет исправление сессий, очередей и протокола завершения. Последовательность и проверки — в ROADMAP.
