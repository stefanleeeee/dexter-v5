import socket
import select
import os
import struct
import wave
import webrtcvad
import datetime
import torch
import re
import json
import numpy as np
import threading
import time
import uuid
from vosk import Model, KaldiRecognizer
from groq import Groq
from session_runtime import CommandSession, CommandCancelled
from cancel_control import CancelControl
from local_config import load_config, redact_log, followup_status
from tts_text_normalizer import normalize_tts_text, remaining_latin_count
from voice_settings import VoiceSettings
from tts_audio import synthesize_samples, encode_pcm
from streaming_reply import stream_reply
from llm_options import completion_options
from dialog_memory import DialogMemory

# === НАСТРОЙКИ ===
try:
    CONFIG = load_config()
except ValueError as error:
    raise SystemExit(f"Configuration error: {error}") from None
HOST = CONFIG.host
PORT = CONFIG.port
GROQ_API_KEY = CONFIG.api_key

groq_client = Groq(api_key=GROQ_API_KEY)

dialog_memory = DialogMemory(CONFIG.dialog_memory_enabled, CONFIG.dialog_memory_max_turns, CONFIG.dialog_memory_max_tokens)

def log(msg):
    timestamp = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    print(f"[{timestamp}] {redact_log(msg, CONFIG)}")

log("[INIT] Загрузка дежурной нейросети Vosk (Wake Word)...")
if not os.path.exists("model_ru"):
    log("[ОШИБКА] Папка 'model_ru' не найдена! Скачай модель Vosk.")
    exit(1)
vosk_model = Model("model_ru")
wake_recognizer = KaldiRecognizer(vosk_model, 16000)
log("[INIT] Дежурный контур готов к поиску имени 'Декстер'.")

log("[INIT] Загрузка локальной нейросети Silero TTS...")
device = torch.device('cpu')
torch.set_num_threads(4)
local_file = CONFIG.tts_model + '.pt'

if not os.path.isfile(local_file):
    log(f"[ОШИБКА] Файл '{local_file}' не найден в папке со скриптом!")
    exit(1)

silero_model = torch.package.PackageImporter(local_file).load_pickle("tts_models", "model")
silero_model.to(device)
voice_settings = VoiceSettings(CONFIG.tts_model, silero_model.speakers, CONFIG.tts_voice)
log(f"[TTS] Model={CONFIG.tts_model}, speaker={voice_settings.snapshot()}, CPU, 24000 Hz, normalization={CONFIG.tts_normalize_en}")
log("[INIT] Голосовой движок успешно загружен в ОЗУ!")
log(f"[MODE] LLM_STREAMING={CONFIG.llm_streaming}")
log('[FOLLOWUP CONFIG] ' + json.dumps(followup_status(CONFIG)))
if CONFIG.dialog_followup_enabled:
    log('[FOLLOWUP] Support is negotiated per TCP session; no window before fresh capture confirmation.')
log(f"[LLM CONFIG] max_completion_tokens={CONFIG.llm_max_completion_tokens}, reasoning_effort={CONFIG.llm_reasoning_effort}, include_reasoning={CONFIG.llm_include_reasoning}")

def send_audio_to_esp32(conn, text_response, session_id=""):
    log(f"[{session_id}] [TTS] Синтез речи (Silero TTS)...")
    t_tts_start = time.time()
    try:
        # Freeze the speaker for this whole reply; console changes affect the next one.
        speaker = voice_settings.snapshot()
        text_response = normalize_tts_text(text_response, enabled=CONFIG.tts_normalize_en)
        remaining = remaining_latin_count(text_response)
        if remaining:
            log(f"[TTS TEXT] {remaining} Latin fragments retained (including protected literals); pronunciation is not guaranteed")
        text_response = text_response.replace('\n', ' ').strip()
        # Bound each model call without dropping the remainder of a long sentence.
        sentences = []
        for sentence in re.split(r'(?<=[.!?])\s+', text_response):
            while len(sentence) > 900:
                boundary = sentence.rfind(' ', 0, 901)
                if boundary <= 0: boundary = 900
                sentences.append(sentence[:boundary])
                sentence = sentence[boundary:].lstrip()
            if sentence.strip(): sentences.append(sentence)
        if not sentences: return True
        
        audio_pieces = []
        for sentence in sentences:
            conn.check_active()
            if not sentence.strip(): continue
                
            samples = synthesize_samples(silero_model, sentence, speaker, conn.check_active)
            audio_pieces.append(samples)
            
        if not audio_pieces: return

        audio_np = np.concatenate(audio_pieces)
        raw_pcm = encode_pcm(audio_np)
        raw_pcm += b'\x00' * (24000 * 2 // 4) 
        
        t_tts_ready = time.time()
        log(f"[{session_id}] [СЕТЬ] Отправка СТАРТ (0x11). Синтез занял: {t_tts_ready - t_tts_start:.2f} сек")
        playback_token = conn.begin_playback()
        
        chunk_size = 4096
        for i in range(0, len(raw_pcm), chunk_size):
            chunk = raw_pcm[i:i+chunk_size]
            header = bytearray([0x12])
            header.extend(struct.pack('>I', len(chunk)))
            conn.sendall(header + chunk)
            time.sleep(0.075)
            
        log(f"[{session_id}] [СЕТЬ] Отправка СТОП (0x13). Передача заняла: {time.time() - t_tts_ready:.2f} сек")
        conn.finish_playback(playback_token)
        return True # Успех
    except CommandCancelled:
        log(f"[{session_id}] [CANCEL] Синтез/передача ответа отменены.")
        return False
    except (ConnectionError, BrokenPipeError, TimeoutError, OSError) as e:
        log(f"[{session_id}] [ОШИБКА СЕТИ] Сокет недоступен или разорван основным потоком: {e}")
        return False # Ошибка сети
    except Exception as e:
        log(f"[{session_id}] [ОШИБКА TTS] {e}")
        return False # Внутренняя ошибка

# Выделяем тяжелую обработку в отдельный поток
def process_command(conn, audio_bytes, state, session=None):
    session_id = state.get("session_id", "SYS")
    temp_in_wav = f"upload_temp_{session_id}.wav"
    memory_version = dialog_memory.version()
    
    try:
        if session is not None:
            session.check_active()
        log(f"[{session_id}] [DSP] Команда записана ({len(audio_bytes)} байт). Нормализация AGC...")
        
        if not audio_bytes or len(audio_bytes) % 2:
            raise ValueError('Command PCM must contain complete PCM16 samples')
        audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
        max_val = np.max(np.abs(audio_np, dtype="int32"))
        if max_val > 0:
            scale = 28000.0 / max_val
            audio_np = np.clip(audio_np * scale, -32768, 32767).astype(np.int16)
        clean_audio = audio_np.tobytes()

        with wave.open(temp_in_wav, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(clean_audio)

        log(f"[{session_id}] [GROQ STT] Распознавание команды (Whisper)...")
        t_stt_start = time.time()
        try:
            if session is not None: session.check_active()
            with open(temp_in_wav, "rb") as file:
                transcription = groq_client.audio.transcriptions.create(
                  file=(temp_in_wav, file.read()), model="whisper-large-v3"
                )
            user_text = transcription.text
            if session is not None:
                session.check_active()
            if not user_text.strip():
                log(f"[{session_id}] [STT EMPTY] No speech text; no LLM/playback/history")
                return
            log(f"[{session_id}] [РАСПОЗНАНО] (за {time.time() - t_stt_start:.2f} с): {user_text}")
        except CommandCancelled:
            return
        except Exception as e:
            log(f"[{session_id}] [ОШИБКА STT] {e}")
            return # Выходим, но finally всё равно сработает!

        try:
            log(f"[{session_id}] [GROQ LLM] Генерация ответа (OpenAI GPT-OSS-120b)...")
            t_llm_start = time.time()
            messages = [
                {"role": "system", "content": "Ты — голосовой ассистент Декстер. Отвечай естественным разговорным русским языком, просто и по существу. По умолчанию давай краткий, но содержательный ответ соразмерно вопросу: простой факт — одно-два предложения, обычный вопрос — примерно два-четыре, объяснение темы — примерно три-шесть. Это ориентиры, а не жёсткие ограничения. По явной просьбе подробно рассказать давай развёрнутый ответ; по возможности соблюдай указанное пользователем число предложений. Просьбы «короче», «в двух словах», «кратко» сокращают ответ; «подробнее», «продолжай», «расскажи больше» расширяют предыдущую тему с учётом контекста. Сохраняй содержательные инженерные объяснения, когда они нужны, без избыточно сложных формулировок. Не повторяй длинные вступления и заключения. Если вопрос неоднозначен, при необходимости уточни его. Не придумывай точные числа, даты и характеристики при отсутствии уверенности; не заменяй неизвестные сведения вымышленными, прямо обозначай неопределённость. Запрещено использовать Markdown (*, #, -, списки). Генерируй только сплошной текст. Все числа пиши словами в обычной речи, сохраняя точные технические данные. Ответ озвучивается русским синтезатором: известные английские названия и сокращения передавай понятной русской фонетической записью, например Python — Пайтон, GitHub — Гитхаб, Windows — Виндоус, Wi-Fi — Вай-фай, NVIDIA — Энвидиа, API — Эй-пи-ай. Не пропускай названия и не подменяй понятия. Не используй латиницу в обычной речи при однозначной фонетической записи. Не выдумывай произношение неизвестных названий. Не изменяй код, URL, email, пути, команды, имена файлов, точные идентификаторы и числа в них ради произношения."}
            ]
            messages, _, memory_metrics = dialog_memory.prepare(
                messages[0]['content'], user_text,
                getattr(CONFIG, 'llm_max_completion_tokens', 2048))
            log('[MEMORY METRICS] ' + json.dumps(memory_metrics))

            if session is not None: session.check_active()
            streaming = getattr(CONFIG, 'llm_streaming', False)
            if streaming:
                response_text, stream_metrics = stream_reply(
                    conn, groq_client.chat.completions.create, messages, silero_model,
                    voice_settings.snapshot(), CONFIG.tts_normalize_en, log,
                    options=completion_options(CONFIG, streaming=True))
                if not response_text.strip():
                    log('[STREAM ПУСТОЙ ОТВЕТ] outcome=' + stream_metrics.get('outcome', 'done') + '; visible_chars=0, без воспроизведения/истории')
                    return
            else:
                chat_completion = groq_client.chat.completions.create(
                    messages=messages, **completion_options(CONFIG)
                )
                response_text = chat_completion.choices[0].message.content or ''
            partial = (stream_metrics.get('outcome') == 'partial_length') if streaming else (getattr(chat_completion.choices[0], 'finish_reason', 'stop') == 'length')
            if streaming and partial:
                log(f"[{session_id}] [STREAM ЧАСТИЧНЫЙ ОТВЕТ: length]\n{response_text}\n[ЛИМИТ] Полученный текст озвучен до конца, продолжение не запрашивается; история не пополнена.")
            elif streaming:
                log(f"[{session_id}] [STREAM ОТВЕТ] (полный цикл до ACK за {time.time() - t_llm_start:.2f} с):\n{response_text}\n=============")
            elif partial:
                log(f"[{session_id}] [LLM ЧАСТИЧНЫЙ ОТВЕТ: length]\n{response_text}")
            else:
                log(f"[{session_id}] [LLM ОТВЕТ] (за {time.time() - t_llm_start:.2f} с):\n{response_text}\n=============")
            
            if not streaming:
                if session is not None: session.check_active()
                success = send_audio_to_esp32(conn, response_text, session_id)
                if session is not None: session.check_active()
                if not success:
                    log(f"[{session_id}] [PLAYBACK ERROR] History not committed")
                    return
            # Both paths have received matching ACK. Session gate orders disconnect
            # against commit; memory version independently orders console clear.
            complete = (stream_metrics.get('outcome') == 'done') if streaming else (
                getattr(chat_completion.choices[0], 'finish_reason', 'stop') == 'stop')
            if complete and response_text.strip():
                def commit_history():
                    return dialog_memory.commit(memory_version, user_text, response_text)
                if session is not None:
                    session.commit(commit_history)
                else:
                    commit_history()
                state['followup_success'] = True
                log('[MEMORY STATUS] ' + json.dumps(dialog_memory.status()))
        except CommandCancelled:
            return
        except Exception as e:
            log(f"[{session_id}] [ОШИБКА LLM] {type(e).__name__}")

    finally:
        # Гарантированная очистка независимо от ошибок и return-ов
        if os.path.exists(temp_in_wav):
            try:
                os.remove(temp_in_wav)
            except Exception as e:
                log(f"[{session_id}] [ОШИБКА ОЧИСТКИ] Не удалось удалить {temp_in_wav}: {e}")
        
        state["mode"] = "WAKE_WORD"
        if session is None:
            log(f"\n[ОЖИДАНИЕ] Декстер снова ждет своего имени...")

def recvall(conn, n, session_id="SYS"):
    data = bytearray()
    deadline = time.monotonic() + 10.0
    while len(data) < n:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([conn], [], [], remaining)[0]:
                raise TimeoutError("Incomplete TCP frame exceeded its deadline")
            packet = conn.recv(n - len(data))
            if time.monotonic() >= deadline:
                raise TimeoutError("TCP frame arrived after its deadline")
            if not packet:
                log(f"[{session_id}] [СЕТЬ] Клиент разорвал соединение. Ждали {n} байт, получили {len(data)}.")
                return None
            data.extend(packet)
        except TimeoutError:
            log(f"[{session_id}] [ОШИБКА СЕТИ] Тайм-аут сокета! ESP32 перестал слать данные.")
            return None
        except Exception as e:
            log(f"[{session_id}] [ОШИБКА СЕТИ] Ошибка recv: {e}")
            return None
    return bytes(data)

def start_server(control=None):
    vad = webrtcvad.Vad(3)
    FRAME_SIZE = 960
    MAX_COMMAND_FRAMES = 233 

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, PORT))
        s.listen()
        log(f"Сервер слушает порт {PORT}...")
        
        while True:
            try:
                conn, addr = s.accept()
                with CommandSession(conn, log, followup_enabled=getattr(CONFIG, 'dialog_followup_enabled', False),
                                    followup_timeout_s=getattr(CONFIG, 'dialog_followup_timeout_s', 12)) as session:
                    if control is not None: control.attach(session)
                    log(f"ESP32 подключен: {addr}")
                    conn.settimeout(10.0)
                    state = session.state
                    command_buffer = bytearray()
                    stream_buffer = bytearray()
                    silence_frames = 0
                    command_frames = 0
                    voiced_frames = 0
                    followup_command = False
                    followup_buffer = bytearray()
                    
                    while True:
                        previous_mode = state['mode']
                        session.poll()
                        if previous_mode != state['mode']:
                            stream_buffer.clear()
                            command_buffer.clear()
                            followup_buffer.clear()
                            followup_command = False
                        current_session = state.get("session_id", "SYS")
                        header = recvall(conn, 5, current_session) 
                        if not header: break
                        msg_type = header[0]

                        if msg_type == 0x18:
                            if struct.unpack('>I', header[1:5])[0] != 0: break
                            session.receive_followup_capability()
                            continue
                        if msg_type == 0x1A:
                            if struct.unpack('>I', header[1:5])[0] != 32: break
                            token = recvall(conn, 32, current_session)
                            if token is None: break
                            session.receive_fresh_capture(token)
                            continue
                        if msg_type in (0x14, 0x16):
                            length = struct.unpack('>I', header[1:5])[0]
                            if length != 32:
                                log('[СЕТЬ] Некорректная длина playback ACK; закрываем соединение.')
                                break
                            token = recvall(conn, length, current_session)
                            if token is None: break
                            accepted = (session.receive_playback_ack(token) if msg_type == 0x14
                                        else session.receive_cancel_ack(token))
                            if not accepted:
                                log('[СЕТЬ] Игнорируем устаревший/неожиданный playback ACK.')
                            continue
                        
                        if msg_type == 0x02:
                            length = struct.unpack('>I', header[1:5])[0]
                            
                            # Диагностика аномальных пакетов от микрофона
                            if length > 32000 or length % 2:
                                log(f"[{current_session}] [ОШИБКА СЕТИ] Аномальная длина пакета микрофона: {length} байт. Игнорируем.")
                                break

                            chunk = recvall(conn, length, current_session)
                            if not chunk: break

                            previous_mode = state['mode']
                            session.poll()
                            if previous_mode != state['mode']:
                                stream_buffer.clear()
                                command_buffer.clear()
                                followup_buffer.clear()
                                followup_command = False
                            
                            if state["mode"] == "PROCESSING":
                                continue

                            stream_buffer.extend(chunk)

                            while len(stream_buffer) >= FRAME_SIZE:
                                frame_bytes = bytes(stream_buffer[:FRAME_SIZE])
                                del stream_buffer[:FRAME_SIZE]
                                
                                pcm_np = np.frombuffer(frame_bytes, dtype=np.int16)
                                vol = np.max(np.abs(pcm_np, dtype="int32"))

                                is_speech = False
                                if vol > 1000:
                                    is_speech = vad.is_speech(frame_bytes, 16000)

                                previous_mode = state['mode']
                                session.poll()
                                if previous_mode != state['mode']:
                                    command_buffer.clear()
                                    followup_buffer.clear()
                                    followup_command = False
                                if state['mode'] == 'FOLLOWUP':
                                    if is_speech:
                                        followup_buffer.extend(frame_bytes)
                                        del followup_buffer[:-3 * FRAME_SIZE]
                                    else:
                                        followup_buffer.clear()
                                    if session.followup_speech(is_speech):
                                        command_buffer = bytearray(followup_buffer)
                                        followup_buffer.clear()
                                        followup_command = True
                                        command_frames = voiced_frames = 3
                                        silence_frames = 0
                                    continue

                                if state["mode"] == "WAKE_WORD":
                                    if wake_recognizer.AcceptWaveform(frame_bytes):
                                        result = json.loads(wake_recognizer.Result())
                                        text = result.get("text", "")
                                        if text: log(f"[VOSK УСЛЫШАЛ]: {text}")
                                    else:
                                        partial = json.loads(wake_recognizer.PartialResult())
                                        text = partial.get("partial", "")
                                        
                                    if "декстер" in text.lower() or "dexter" in text.lower():
                                        log("\n[АКТИВАЦИЯ] Имя 'Декстер' распознано! Слушаю команду...")
                                        state["mode"] = "COMMAND"
                                        state["session_id"] = str(uuid.uuid4())[:6]
                                        command_buffer.clear()
                                        silence_frames = 0
                                        command_frames = 0
                                        voiced_frames = 0
                                        followup_command = False
                                        wake_recognizer.Reset() 

                                elif state["mode"] == "COMMAND":
                                    command_buffer.extend(frame_bytes)
                                    command_frames += 1
                                    
                                    if is_speech:
                                        voiced_frames += 1
                                        silence_frames = 0
                                        print("=", end="", flush=True)
                                    else:
                                        silence_frames += 1
                                        print("-", end="", flush=True)
                                        
                                    if silence_frames > 50 or command_frames > MAX_COMMAND_FRAMES:
                                        print() 
                                        if len(command_buffer) > 16000 and (not followup_command or voiced_frames >= 10):
                                            log("[СТОП] Запись завершена. Отправка в Groq...")
                                            state["mode"] = "PROCESSING"
                                            # Остаток текущего payload относится к старой записи.
                                            stream_buffer.clear()
                                            try:
                                                session.start(process_command, bytearray(command_buffer), thread_factory=threading.Thread)
                                            except Exception:
                                                state["mode"] = "WAKE_WORD"
                                                raise
                                        else:
                                            log("[СБРОС] Слишком короткая команда.")
                                            session.end_followup_capture()
                                            state["mode"] = "WAKE_WORD"
                                            log("\n[ОЖИДАНИЕ] Декстер снова ждет своего имени...")
                                            stream_buffer.clear()
                                        command_buffer.clear()
                                        break
            
            except (ConnectionError, ConnectionResetError) as e:
                log(f"[СЕТЬ] Обрыв связи с ESP32: {e}. Ждем переподключения...")
            except Exception as e:
                log(f"[ОШИБКА СЕРВЕРА] {e}")

if __name__ == "__main__":
    cancel_control = CancelControl(log, voices=voice_settings, memory=dialog_memory)
    cancel_control.start()
    start_server(cancel_control)
