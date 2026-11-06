# AI Dexter contributor instructions

Preserve the existing voice pipeline: microphone PCM16LE mono16k → Vosk wake word → Groq STT/LLM → Silero aidar24k → ESP32 I2S. Do not import server.py in offline tests: import loads models and initializes the cloud client.

Read PROJECT.md, ROADMAP.md, STATUS.md and ARCHITECTURE.md before work. Historical snapshots are retained; newest status takes priority. Phase 1 tasks 1.1–1.9 and Phase 2 New TTS (Silero V5.6 CPU, voice console, phonetic adaptation) are implemented and accepted. Phase 3 Streaming Voice Responses is implemented and accepted; Phase 4 dialogue context/natural conversation is implemented and accepted; Phase 5 Windows control is next and not implemented. CosyVoice is not integrated.

CommandSession owns each connection, one worker writer, one socket reader, a global pipeline lock, generation/UUID and full response IDs. Preserve cancellation and stale-result guards. Never confuse send complete, queue empty and I2S completion. ESP playback consumer alone calls TX I2S; never hold its mutex during driver calls. Do not change PCM/rates/I2S/queue size casually.

Keep .env and esp32/main/dexter_local_config.h local and ignored. Never publish models, audio, build artifacts, backups, raw logs, private paths or credentials. Examples must contain placeholders only. Do not install dependencies, rotate credentials, flash hardware or publish without task authorization.

Keep changes narrow, create backups outside the repository, add focused offline tests, run regressions and build ESP if changed. Preserve user modifications/history. Update STATUS.md with actual test evidence; never claim unperformed hardware tests. Do not start CosyVoice without a separate task. Do not spawn extra agents without explicit permission.

Streaming modules: llm_stream, phrase_buffer, tts_audio, streaming_reply, llm_options.
Preserve partial_length EOS/ACK behavior without automatic CANCEL; empty_length
must not start audio. Partial responses are not committed as complete history.
Budget/reasoning/stream flags are local config; do not log reasoning or credentials.

Phase4: preserve DialogMemory RAM bounds, whole-pair atomicity, compact markers,
clear/version guards and commit only after complete stop/done+ACK. Never treat
partial replies as completed history. Follow-up is part of the receiver state
machine, not a second writer/reader. Probe0x17/support0x18 must precede fresh
request0x19/ready0x1A; keep full reply/session ID and capability/eligibility checks.
Freshness depends on generation/ring/RX barrier and READY-before-enqueue order,
not sleep alone. Only mic task calls RX driver, outside mutex; only TCP writes
ESP socket. Legacy/noREADY must remain wake-only. No AEC/barge-in is implemented.
Hardware evidence is limited to the explicitly recorded owner acceptance.
