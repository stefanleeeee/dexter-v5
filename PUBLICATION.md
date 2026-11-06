# Phase 3 stable publication review

Accepted milestone: Streaming Voice Responses. Target main; one commit
`feat: Phase 3 - Streaming Voice Responses`; annotated tag
`v0.3.0-streaming-stable`. Prior history/tags are retained. Phases1–3 complete;
Phase4 dialogue context/natural conversation is next, not implemented.

The owner reports short/long ESP32 streaming playback, first phrase transmission
about0.6–1.2s after LLM start, and a10-sentence/~110s reply with stop/done/PLAYBACK
DONE. These are owner-reported results; no new acoustic/Reset/stall measurements
are invented. Publication itself performs no real Groq requests or hardware flash.

Only allowlisted Python source/tests, safe .env.example, README/docs are updated.
Firmware, dependency pins, weights and local settings remain unchanged. Local
configs are never overwritten. MANIFEST.txt lists the entire proposed tree.

Exclude filled .env/Wi-Fi headers, generated sdkconfig/build/binaries, .pt/model
weights, Vosk data, audio/reference/WAV, logs, venvs, backups, caches and private
raw benchmark/environment reports. Ignore tests supplement, not replace, content
inspection. Check staged list/full diff, known secrets without printing values,
private paths/accounts, token/private-key patterns, sizes/binary/reference content,
then commit/tag and every Git object. Automated scanning is not an absolute guarantee.
No dedicated scanner installation is required for this audit.

Regression target:224 tests via unittest discovery, including mode/config,
stop/partial_length/empty_length/cancelled/error, EOS/ACK/CANCEL and history rules,
phrase order, bounded buffers/backpressure, stale audio, normalizer and rollback.
README documents LLM_STREAMING, LLM_MAX_COMPLETION_TOKENS, LLM_REASONING_EFFORT,
LLM_INCLUDE_REASONING, startup/env precedence and local trusted weight deployment.
No clean network dependency installation is claimed.

Remote main must still equal the known phase2 commit before push. If the tag
exists unexpectedly or history diverges, stop. Push only main and this annotated
tag without force; anonymously verify the branch/tag/commit and every public blob
against the audited tree. Local reports/backups remain outside this repository.
