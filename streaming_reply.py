"""Pull pipeline: one phrase/TTS call/PCM buffer, existing worker is sole writer."""
import json
import struct
import time

from llm_stream import GroqTextStream
from llm_options import completion_options, usage_numbers
from session_runtime import CommandCancelled
import re
from phrase_buffer import PhraseBuffer
from tts_audio import synthesize_samples, encode_pcm, bounded_tts_parts
from tts_text_normalizer import normalize_tts_text, remaining_latin_count


class PCMReply:
    def __init__(self, conn, metrics, clock, started):
        self.conn, self.metrics, self.clock, self.started = conn, metrics, clock, started
        self.token = None
        self._last_send = None

    def send(self, pcm):
        if not pcm:
            return
        if len(pcm) % 2:
            raise ValueError('PCM must contain complete PCM16 samples')
        self.conn.check_active()
        if self.token is None:
            self.metrics['first_start_s'] = self.clock() - self.started
            self.token = self.conn.begin_playback()
        now = self.clock()
        if self._last_send is not None:
            gap = now - self._last_send
            self.metrics['producer_gap_count'] += 1
            self.metrics['producer_gap_total_s'] += gap
            self.metrics['producer_gap_max_s'] = max(self.metrics['producer_gap_max_s'], gap)
        for offset in range(0, len(pcm), 4096):
            self.conn.check_active()
            chunk = pcm[offset:offset + 4096]
            if self.metrics['first_send_s'] is None:
                self.metrics['first_send_s'] = self.clock() - self.started
            begin = self.clock()
            self.conn.sendall(struct.pack('>BI', 0x12, len(chunk)) + chunk)
            self.metrics['sendall_s'] += self.clock() - begin
            self.metrics['pcm_bytes'] += len(chunk)
        self._last_send = self.clock()

    def finish(self):
        if self.token is not None:
            self.send(bytes(12000))  # Preserve only the single final 250 ms tail.
            begin = self.clock()
            self.conn.finish_playback(self.token)  # real EOS + matching ACK
            self.metrics['ack_wait_s'] = self.clock() - begin
            self.metrics['ack_s'] = self.clock() - self.started


def stream_reply(conn, create, messages, model, speaker, normalize, log,
                 clock=time.perf_counter, options=None):
    started = clock()
    metrics = dict(first_text_s=None, first_phrase_s=None, first_tts_s=None,
                   first_pcm_s=None, first_start_s=None, first_send_s=None,
                   llm_complete_s=None, ack_wait_s=None, ack_s=None,
                   tts_s=0.0, audio_s=0.0, pcm_bytes=0, sendall_s=0.0,
                   phrases=0, queued_phrases=0, max_inflight_phrases=1,
                   max_pcm_buffer_bytes=0, text_buffer_high=0,
                   producer_gap_count=0, producer_gap_total_s=0.0,
                   producer_gap_max_s=0.0, outcome='error')
    request_options = completion_options(streaming=True) if options is None else dict(options)
    metrics.update(finish_reason=None, visible_chars=0, completed_phrases=0,
                   max_completion_tokens=request_options.get('max_completion_tokens'),
                   reasoning_effort=request_options.get('reasoning_effort'),
                   include_reasoning=request_options.get('include_reasoning'),
                   usage=usage_numbers(None), cancel_source=None, error_stage=None)
    buffer = PhraseBuffer()
    writer = PCMReply(conn, metrics, clock, started)
    response = None
    phase = 'groq'

    def speak(raw):
        nonlocal phase
        phase = 'tts_prepare'
        conn.check_active()
        if not raw.strip():
            return
        if metrics['first_phrase_s'] is None:
            metrics['first_phrase_s'] = clock() - started
        prepared = normalize_tts_text(raw, enabled=normalize)
        remaining = remaining_latin_count(prepared)
        if remaining:
            log(f'[TTS TEXT] {remaining} Latin fragments retained (including protected literals); pronunciation is not guaranteed')
        for part in bounded_tts_parts(prepared):
            phase = 'tts'
            begin = clock()
            samples = synthesize_samples(model, part, speaker, conn.check_active)
            elapsed = clock() - begin
            metrics['tts_s'] += elapsed
            if metrics['first_tts_s'] is None:
                metrics['first_tts_s'] = elapsed
            pcm = encode_pcm(samples)
            conn.check_active()
            if metrics['first_pcm_s'] is None:
                metrics['first_pcm_s'] = clock() - started
            metrics['max_pcm_buffer_bytes'] = max(metrics['max_pcm_buffer_bytes'], len(pcm))
            metrics['audio_s'] += len(pcm) / 48000.0
            metrics['phrases'] += 1
            phase = 'tcp'
            writer.send(pcm)
            del samples, pcm
            # Do not retain prior phrase samples/PCM, and do not read ahead while sending.

    try:
        with GroqTextStream(create, conn.check_active, messages=messages, clock=clock,
                            options=request_options) as response:
            for delta in response:
                phase = 'phrase_buffer'
                for phrase in buffer.feed(delta):
                    speak(phrase)
                    metrics['completed_phrases'] += 1
                phase = 'groq'
            # stop and length both drain received visible text, never invent a continuation.
            tail = buffer.finish()
            speak(tail)
            if re.search(r'[.!?…][»"\x27)\]]*\s*$', tail):
                metrics['completed_phrases'] += 1
            conn.check_active()
            phase = 'ack'
            writer.finish()
            metrics['outcome'] = ('partial_length' if response.text.strip() else 'empty_length') if response.finish_reason == 'length' else 'done'
            return response.text, metrics
    except BaseException as error:
        buffer.discard()
        source = response.cancel_source if response is not None else None
        try:
            conn.check_active()
        except CommandCancelled:
            source = 'user_cancel'
        except ConnectionError:
            if source is None and not isinstance(error, TimeoutError): source = 'session_inactive'
        if isinstance(error, CommandCancelled): source = 'user_cancel'
        metrics['outcome'] = 'cancelled' if source else 'error'
        metrics['cancel_source'] = source
        metrics['error_stage'] = phase
        metrics['error_type'] = type(error).__name__
        if response is not None and response.text.strip():
            log('[STREAM ЧАСТИЧНЫЙ ТЕКСТ: ' + metrics['outcome'] + ']\n' + response.text)
        if writer.token is not None and not source:
            # Only actual failures request automatic CANCEL. Length never enters here.
            metrics['cancel_source'] = 'error_cleanup'
            log('[STREAM AUTO CANCEL] cause=error stage=' + phase)
            conn.request_cancel(expected_id=writer.token)
        raise
    finally:
        if response is not None:
            metrics['first_text_s'] = response.first_text_s
            metrics['llm_complete_s'] = response.completed_s
            metrics['finish_reason'] = response.finish_reason
            metrics['visible_chars'] = len(response.text)
            metrics['usage'] = response.usage
        metrics['text_buffer_high'] = buffer.high_water
        metrics['rtf'] = metrics['tts_s'] / metrics['audio_s'] if metrics['audio_s'] else None
        log('[STREAM METRICS] ' + json.dumps(metrics, sort_keys=True))
