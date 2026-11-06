# AI Dexter contributor instructions

Preserve the existing voice pipeline: microphone PCM16LE mono16k → Vosk wake word → Groq STT/LLM → Silero aidar24k → ESP32 I2S. Do not import server.py in offline tests: import loads models and initializes the cloud client.

Read PROJECT.md, ROADMAP.md, STATUS.md and ARCHITECTURE.md before work. Historical snapshots are retained; newest status takes priority. Phase 1 tasks 1.1–1.9 are implemented. CosyVoice is not integrated.

CommandSession owns each connection, one worker writer, one socket reader, a global pipeline lock, generation/UUID and full response IDs. Preserve cancellation and stale-result guards. Never confuse send complete, queue empty and I2S completion. ESP playback consumer alone calls I2S; never hold its mutex during driver calls. Do not change PCM/rates/I2S/queue size casually.

Keep .env and esp32/main/dexter_local_config.h local and ignored. Never publish models, audio, build artifacts, backups, raw logs, private paths or credentials. Examples must contain placeholders only. Do not install dependencies, rotate credentials, flash hardware or publish without task authorization.

Keep changes narrow, create backups outside the repository, add focused offline tests, run regressions and build ESP if changed. Preserve user modifications/history. Update STATUS.md with actual test evidence; never claim unperformed hardware tests. Do not start CosyVoice without a separate task. Do not spawn extra agents without explicit permission.
