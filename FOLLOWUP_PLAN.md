# Follow-up v1 — реализация4.3 и аппаратная приёмка

Разрешение на ESP/минимальное расширение протокола получено в задании4.3.
Сервер и прошивка реализованы, автоматические проверки/сборка прошли.
Плату прошил и проверил пользователь; этап 4 принят. Агент плату не прошивал.
## Этап 4 завершён и принят пользователем

Пользователь подтвердил аппаратную приёмку памяти, компактных записей, политики
длины и безопасного follow-up. В последнем тесте зафиксированы FRESH CAPTURE
REQUESTED/CONFIRMED, FOLLOWUP WINDOW OPENED/ACCEPTED, успешные уточнения без имени,
PLAYBACK DONE, WINDOW EXPIRED и ограничение истории шестью парами.
Это подтверждение владельца, не новый аппаратный тест при публикации. Дополнительные
Reset/акустические/длительные сценарии не объявляются проверенными без отдельных
данных. Следующий этап 5 — управление Windows; он не реализован и не начат.
Эта запись приоритетнее исторических pending/blocked ниже.



## Проверенная причина и граница свежести

ESP непрерывно читает MIC; wake word/VAD/state принадлежат Python. Старый DONE
доказывает физическое опустошение TX, но не свежесть mic queue. В старом TCP-цикле
ACK предшествовал следующему mic dequeue: после ACK мог идти звук собственного
ответа. Очистка только Python stream_buffer или фиксированный sleep этого не решают.

Теперь после normal DONE и successful outcome worker запрашивает отдельный fresh
capture. На ESP запрос принимается только для полного ID последнего фактически
отправленного normal DONE, однократно. До DONE, послеCANCEL, со старымID/connection
поколение микрофона не меняется. START/CANCEL инвалидируют eligibility старогоID.

Запрос под существующим mic mutex вращает generation, очищает audio_ringbuf,
ставит DRAIN. Producer, захвативший прежнее поколение перед i2s_read, не сможет
enqueue после запроса. Capture generation=0 во время DRAIN/READY: uplink paused.
Единственная задача микрофона после текущего read ждёт300ms акустического хвоста,
запрашивает реальную total_dma_buf_size, делает RX disable/enable, читает и
отбрасывает total_dma_buf_size+1024bytes с budget1s и20ms/read. Normal read теперь
имеет50ms timeout для отклика на запрос. Частоты/slots/DMA размеры не менялись.

Для текущего ESP-IDF6.1 проверен локальный i2s_common.c: disable сбрасывает
curr_ptr/rw_pos, enable сбрасывает RX msg_queue. Вычитывание больше DMA capacity
после restart обеспечивает дополнительную границу против остаточного DMA.
Все driver calls вне mutex и только в mic task. При READY producer ещё не enqueue;
TCP отправляет0x1A и только после полной успешной отправки снимает паузу.
TCP остаётся единственным writer. Поэтому старые MIC не могут пересечь READY.
Это цифровая свежесть, не акустическое эхоподавление.

## Дополнительные кадры

Header прежний5bytes, rawPCM16LE mono16k MIC /24k speaker прежний.
0x17 zero-length Python→ESP:probe v1;0x18 zero-length ESP→Python:capability.
0x19 с32bytes полного response UUID/counter Python→ESP:fresh request.
0x1A с тем же32bytes ESP→Python:fresh ready.0x11..0x16 не менялись.

Probe отправляет только command worker при включённом follow-up, один раз наTCP.
Старая проверенная прошивка игнорирует незнакомый zero-body control. Пока нет0x18,
0x19 не отправляется. Новая прошивка не сообщает capability без probe: со старым
сервером не создаёт неожиданных пакетов. READY принимается единственным reader,
только при ожидаемом полномID текущей сессии; stale/duplicate вне ожидания игнорируются.

ESP fresh deadline2s; Python wait2.5s. Нет READY — нет окна. Частичная TCP отправка
закрывает соединение. Ошибка RX API/read ставит sticky flag доReset, false READY
не выдаётся. Reconnect инвалидирует mic generation/очереди и TCP fresh flags.
Старый producer completion после reconnect не подтверждает новый запрос.

## Единственная машина состояний

Существующий receiver mode:WAKE_WORD→COMMAND→PROCESSING(включая playback/freshwait)
→FOLLOWUP либоWAKE_WORD. Worker только пишет outcome в собственную копию state.
В process_command followup_success выставляется только для непустого full stop/done
после нормального ACK;partial_length/empty_length/cancel/error её не выставляют.
После этого worker может запросить freshness. Receiver открывает окно после
READY, освобождения writer/_PROCESS_LOCK и done event; не раньше.

Deadline по monotonic,12s по умолчанию, не обновляется шумом.3 consecutive
VAD frames дают speech onset90ms; они включаются в command buffer. Для отправки
STT нужно>=10VADframes/300ms речи; shortnoise discarded, окно не повторяется.
Остальные COMMAND thresholds прежние. Один worker за раз. Новый successful
follow-up проходит тот же Whisper→LLM→TTS→ACK и может открыть следующее окно.

CANCEL вFOLLOWUP/COMMAND/freshwait и memory clear передают сигнал session gate;
mode по-прежнему меняет только receiver. Timeout/reconnect/error закрывают окно,
таймаут не стирает память. RAM memory/version guards/compact logic не менялись.
DIALOG_FOLLOWUP_ENABLED=false отключает probe/fresh/window и возвращает старый путь.
Startup supported=false означает «ещё не negotiated», а не постоянную блокировку4.2.

## Проверено автоматически

304tests:274 прежних +14clock/lifecycle +4actual reader/socketpair +12Chelpers.
В существующем.venv221Python-tests;83C-tests в окружении с llvmlite/clang.
Ctests исполняют фактические helpers через JIT со stubI2S/ring/TCP, это не плата.
Проверены старыйMICпослеACK доREADY, staleIDs, legacy/noREADY, абсолютная12sграница,
повторное окно,noise/empty/error,disabled,clear/cancel/reconnect,отсутствиеparallel.
Прежние STT/LLM/TTS/playbackerror,CANCEL/ACK/history regressions сохранены.
Сборка ESP32-S3/bootloader/app успешна:app0xca470,21%раздела свободно.

## Неподтверждённое и ручной сценарий

Без тяжёлого AEC пауза300ms не гарантирует подавления любой реверберации. Говорить
следует послеWINDOW OPENED; речь сразу после звука доfreshready может потеряться.
VAD не отличает владельца от чужой речи/музыки. Предел300ms может отсечь краткое«да».
Это аппаратные ограничения, не объявляются проверенными mock-тестами.

Обязательны:Марс→DONE/FRESH/OPENED→уточнение безимени;>12sбезимени игнорируется,
послеимени работает;длинный ответ безсамоактивации;quiet/noise/эхо/ранняя речь;
CANCEL вplayback/freshwait/window;memoryclear вответе/окне;Reset/reconnect приfresh
иwindow;disabled;heap/errors/micdrop. Снять логи Python иESP синхронно. Инструкции
ручногоflash/rollback — README. Новых APIвызовов, пакетов и flash от агента нет.
