# Phase 3 — Streaming Voice Responses (accepted stable)

After the length-handling fix, streaming is enabled by the requested local/default
configuration. Stop any running server with Ctrl+C, verify these local `.env`
settings, and restart from the project directory:

```dotenv
LLM_STREAMING=true
LLM_MAX_COMPLETION_TOKENS=2048
LLM_REASONING_EFFORT=low
LLM_INCLUDE_REASONING=false
```

```powershell
.\.venv\Scripts\python.exe -B server.py
```

The startup `[MODE]` line reports the effective flag. OS environment variables
override .env. Set `LLM_STREAMING=false` and restart to restore full LLM → full
TTS → transfer. Voice commands and the selected Silero V5.6/CPU/rate remain the
same in both modes. No firmware update, installation, second TTS or cloud service
is required. Do not run a second server on the ESP port.

## Pipeline and bounds

The existing CommandSession worker is the sole ESP writer and retains the global
pipeline/model/history lock. It pulls Groq `stream=True` deltas, segments complete
phrases, normalizes a TTS-only copy, calls Silero synchronously, and sends that
phrase's PCM. It does not wait for all LLM text or all audio. No producer TTS
thread/async audio queue is added: queued phrases=0, one phrase in flight.
When sendall blocks, no subsequent TTS call or HTTP delta read is started.

This is incremental LLM + phrase-level TTS, not native Silero audio-out streaming.
The TTS API still synthesizes an entire phrase per call. Speaker selection is
snapshotted once before the streaming response. Review `voice status` before use.

Original deltas are retained verbatim, up to32768 characters (additional safety
cap, completion-token budget is configurable and defaults to2048). The phrase buffer is bounded to900 characters;
ordinary sentence punctuation requires lookahead and recognizes common
abbreviations, initials, decimals and dotted identifiers. Long phrases prefer a
comma/semicolon after400 characters, then whitespace near900. A single unbroken
token exceeding the bound raises an explicit error instead of cutting its word.
These are linguistic heuristics, not a universal code/URL/language parser.
Prepared phonetic expansion is re-split safely at whitespace to<=900 characters.
Protected technical literals and unknown Latin retain the normalizer's limits.

One phrase output is capped at2400000 samples (100s at24kHz), checked mono/finite,
clipped and encoded PCM16LE. Only current samples/PCM are retained; prior buffers
are released. No explicit silence is inserted per phrase. The existing single
250ms final tail is retained. Stable mode shares these sample/PCM helpers while
keeping its original full-buffer behavior and existing75ms pacing. Streaming adds
no pacing sleep: the existing network/ESP queue supplies backpressure.

## Protocol, cancellation and errors

One START is sent only when first PCM is ready. All phrases use its full playback
ID, chunks remain4096 bytes or smaller/even, and one EOS follows the final tail.
Only the existing matching ACK finishes a normal response; no extra ESP reader
or writer is created. An empty normal answer produces no playback or history.

The console still accepts cancel/отмена. Active checks occur before HTTP reads,
before/after TTS and between frames. A small daemon watcher attempts to close only
the HTTP stream when the command/disconnect becomes inactive; it never touches
the ESP socket or model. SDK.close closes its response. Existing Groq timeouts
(read60s/connect5s, retries2) remain; an opening SDK request before headers and an
already running Silero call cannot be forcibly killed. Close is best effort,
and cleanup relies on the existing bounded CANCELLED timeout/disconnect fallback.

On manual CANCEL, pending text is discarded and no tail is flushed. A true API,
TTS or transport failure after partial audio requests the existing CANCEL cleanup;
logs label the automatic error-cleanup source separately. A watcher retains the
first observed user-cancel source even if TCP fallback subsequently closes the
connection. Disconnect/stale generation is distinct from a user request.

`finish_reason=length` is a partial result, not an exception/network failure. All
received visible text, including the unfinished last phrase exactly as received,
is drained through TTS; no continuation or grammatical ending is invented. One
EOS/ACK completes that audio. The outcome is partial_length; the received text is
printed under STREAM ЧАСТИЧНЫЙ ОТВЕТ and is **not** added as a completed history
pair. If no visible non-whitespace text exists, outcome=empty_length with no START,
TTS, EOS or ACK. Normal stop is done. Missing finish/content_filter/tool_calls and
actual exceptions remain failures. Empty normal answers likewise do not play audio.

Normal completed streaming history commits the complete original visible text only
after success ACK. Partial answers are returned to the server/printed, not silently
turned into successful conversation history. Full-response fallback also skips a
length-limited history pair. Both modes use the same configurable budget/effort;
mode rollback does not reset the new budget to the old500-token value.

The installed SDK1.7 supports include_reasoning=False; reasoning_format is never
sent alongside it. This controls returned reasoning, not whether reasoning tokens
are computed. Only delta.content is treated as user text; delta.reasoning is never
printed or spoken. max_tokens was replaced with max_completion_tokens, and
reasoning_effort defaults tolow (supported choices low/medium/high). Budget accepts
1..32768 locally; common values2048/3072/4096. This is a local configuration guard,
not a claim about the model's context maximum. The existing32768-character text
safety bound is separate. No model/temperature/key/SDK timeouts/retry changes.

Change LLM_MAX_COMPLETION_TOKENS to3072 or4096 and restart if partial_length remains
frequent. Environment variables override .env: if a PowerShell variable was set,
change it there too or remove it before relying on .env. No auto-continuation or
additional API requests are made when the limit is reached.


## Metrics and checks

`[STREAM METRICS]` logs numbers/status, not deltas: finish_reason, visible_chars,
completed_phrases (segmenter-closed phrases, a terminal-punctuated tail also counts),
actual max_completion_tokens/effort/include flag, usage and outcome plus first text/phrase/TTS/PCM,
START/send, observed LLM end, TTS seconds/RTF, PCM bytes/buffer high-water,
queued/in-flight phrases, total sendall wall time and ACK wait/end.
`llm_complete_s` means **when this pull reader observes normal EOF**. It can lag
the provider's actual generation while TTS/TCP applies backpressure. sendall_s
includes overhead and blocking, not a pure packet-loss/queue-pressure counter.
Producer gaps include time between PCM groups and the final tail; they are not
measured acoustic underruns. START/send is not first sound; ACK is a software DMA
drain confirmation, not an acoustic sensor. Short replies can finish generating
before any audio is ready even with streaming.

The phase3 regression suite contains224 tests, including the earlier209 tests and15 length/usage/history
checks. Final usage is read from chunk.usage or x_groq.usage, including choices=[]
chunks. SDK1.7 has no named stream_options parameter, so its documented extra_body
mechanism sends stream_options.include_usage=true. Missing usage fields are null,
never inferred. A one-request verification of this fix returned stop with23 visible
characters: prompt97/completion27/reasoning11 tokens, first text0.523s/end0.539s.
No reasoning text was logged. The earlier phase3 probe below is historical. One short real Groq request
was made with retries disabled for that probe: first text0.726s, end0.740s,
five deltas/finish stop. No other live Groq benchmark was performed.

Initial phase3 benchmark (before this budget fix; not rerun because TTS is unchanged):
real Silero V5.6 CPU + artificial absolute-time LLM schedule and a simulated
bounded341ms playback queue were compared once per mode after warmup:

| Case | Full first PCM send | Streaming first PCM send | Earlier by |
| --- | ---: | ---: | ---: |
| Short | 2.238s | 1.360s | 0.878s |
| Long | 5.592s | 1.372s | 4.220s |

The artificial provider end was1.8s/3.6s, so streaming first sends preceded these
scheduled ends. Simulated ACK was8.646/7.768s (short full/stream) and
33.887/29.977s (long). Streaming first phrases arrived at1.200s; first synthesis
took0.159/0.171s; RTF about0.07. Peak process working set788.5MiB (TTS-only, no
Vosk), CUDA not initialized. Mean process CPU over the waiting-heavy simulated
cycles was0.7–2.8% (100%=one logical CPU), not a measured inference peak.
These are controlled local diagnostics, **not live Groq-to-ESP or acoustic speedup**.
Full raw private reports remain in baseline/phase3 and are not published.

## Manual acceptance

1. Restart with streaming=true; confirm model/voice and `[MODE]` output.
2. Ask a short question, then a multi-sentence long one. Record sound and Python/
   ESP logs together. Determine whether speech begins before the remaining reply
   is available; the pull-reader EOF timestamp alone cannot prove provider timing.
3. Ask about Python/GitHub/Windows/API/ESP32 and compare voices/normalization.
4. Cancel before first phrase, during speech and during a later TTS phrase. Check
   CANCELLED or explicit fallback, no false DONE, no old audio in the next reply.
5. Give the next command, reset/reconnect ESP, then ask again. Check IDs and
   queue/errors/mic_drop/heap deltas. An empty queue/pause alone is not packet loss.
6. Repeat the same requests with streaming=false. Listen for inter-phrase pauses,
   missing/repeated words and compare first sound; report acoustic timing limits.

Long/slow phrases can underrun the limited ESP queue: this pull pipeline favors
bounded memory and existing ownership over aggressive pre-generation. The owner reports accepted short/long streaming playback, including a roughly110-second response with finish_reason=stop, outcome=done and PLAYBACK DONE. No additional Reset/stall scenario is claimed solely from that report.

## Rollback

Mode-only rollback: streaming=false + restart, with the same V5.6 file/settings.
Full source rollback: stop the server; restore server.py, local_config.py, .env,
.env.example and test_command_states.py from backups/before-phase3-streaming.
Restore docs if needed. New helper modules are inert when the restored server
does not import them. Private keys/voice/model values were preserved. The public
v0.2.0-tts-stable checkout/tag is unchanged; no commit/push was performed.

## Length-fix rollback and manual queries

The prior source/config is saved outside the public repo in
backups/before-stream-length-fix. For exact rollback stop the server and restore
server.py, local_config.py, llm_stream.py, streaming_reply.py, .env/.env.example and
test_command_states.py from there; new llm_options.py is then unused. Model/voice,
normalizer, STT/Vosk, session runtime and firmware were not changed by this fix.
Mode-only rollback is simply LLM_STREAMING=false and restart.

Manual requests: (1) «Как меня слышно? Ответь коротко»; (2) «Объясни в пяти
предложениях, для чего используют Python и GitHub»; (3) «Расскажи подробно о
планетах Солнечной системы, примерно десять–двенадцать предложений».
Expected: normal stop/done or explicitly partial_length with visible partial text
and EOS/ACK, no automatic CANCEL merely because of length. empty_length means the
budget ended without visible text; no empty playback should start. Then separately
check manual cancel and the following command. Confirm startup LLM CONFIG values.
The owner has accepted the streaming and length-fix hardware behavior. The scenarios above remain a deployment checklist; no new firmware flash is needed.
