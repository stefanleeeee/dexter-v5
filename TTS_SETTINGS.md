# Silero voices and TTS-only phonetic adaptation

Current state (2026-11-06): **V5.6 is active by default**. The owner supplied
`v5_6_ru.pt` and explicitly confirmed download from the official models.silero.ai
site. Its 147664410-byte torch.package ZIP passed full CRC/static format review;
SHA256: `04cc9b1d08ce38bcaa9d02c488d80d3e6806d17f7eaa7c95982f36a3cbf50b2e`.
It loads and synthesizes on the existing CPU-only torch 2.15.0 environment with
all five named voices. No model download or dependency change was performed.
Phase 2 is accepted by the owner. Optional V4 rollback requires your own trusted local `v4_ru.pt`; neither model is distributed.


Run from the normal project directory:

```powershell
.\.venv\Scripts\python.exe -B server.py
```

Startup reports the selected model, speaker, CPU, 24000 Hz and normalization.
One model is loaded. The console uses the existing reader for both cancellation
and voice commands:

```text
voice list
voice status
voice eugene
voice aidar
voice kseniya
voice baya
voice xenia
cancel
```

The model is not reloaded when selecting a voice. A TTS response snapshots its
speaker once: a console change affects the next response, never later sentences
of the one already synthesizing/playing. The virtual `random` speaker is excluded
because it cannot guarantee a fixed voice across calls. Console selection lasts
until restart; use local `.env` for the startup choice:

```dotenv
TTS_MODEL=v5_6_ru
TTS_VOICE=aidar
TTS_NORMALIZE_EN=true
```

Environment variables override `.env`. Model selection requires a restart;
there is no live model reload. The recognized model names are `v4_ru` and
`v5_6_ru`, mapped to local `.pt` files. A missing selected file fails visibly:
there is no silent fallback or automatic download. V5.6 was verified at 24000 Hz
on CPU; runtime speakers are aidar, baya, kseniya, eugene, xenia. Other officially
listed rates are not enabled in Dexter. Retain original source information when
replacing the package; SHA256/CRC alone do not prove the source is trustworthy.
The local hash above is recorded, not claimed to be an independently published
or signed checksum. Official model metadata:
https://github.com/snakers4/silero-models/blob/e382298387332f2bf461fe6eadfde99104611b0c/models.yml

For a model-only rollback, stop the server, set `TTS_MODEL=v4_ru` in local `.env`
(or the environment), then restart. For V5.6 use `TTS_MODEL=v5_6_ru`. Do not load
both versions in the same process. The earlier official GitHub desktop installer
was not used; it is a different format/model.


`tts_text_normalizer.py` replaces known terms only in a TTS copy: Python, GitHub,
Windows, ChatGPT, NVIDIA, API, Wi-Fi, ESP32, USB, CPU, GPU, RAM, TCP, HTTP, JSON.
Matching is case-insensitive and respects word/identifier boundaries. Already
adapted Cyrillic stays unchanged; original LLM text/history is not rewritten.
Unknown Latin words remain and diagnostics report only a count, not their text.
URLs, email, paths, filenames, backtick/fenced code and recognized CLI commands
are preserved. Arbitrary unmarked programming languages/commands cannot be parsed
reliably: use backticks/code fences for exact literals. Such protected or unknown
Latin content may still be skipped by Russian TTS. This is not a universal English
pronunciation solution. Extend `TERMS` for reviewed spellings; do not add blind
letter transliteration. `TTS_NORMALIZE_EN=false` disables this local layer.

The Groq system instruction also requests known phonetic spellings in ordinary
Russian speech while preserving code, URLs, commands and exact identifiers.
Existing concise/engineering/no-Markdown instructions remain. Compliance is
probabilistic; only mocked request arguments were checked, no paid requests made.
No SSML/rate-control UI or streaming was added. The installed V4 API exposes SSML,
stress and ё controls; V5.6 exposes extra homograph/stress/type_str/intensity arguments; no speed-control UI was added and these defaults remain unchanged.

Hardware acceptance was reported by the owner; repeat this smoke after deployment: restart the server,
confirm `voice status` reports v5_6_ru, select eugene, ask «Декстер, расскажи, для чего используют
Python и GitHub». Listen for the names, repeat with aidar/kseniya/other voices.
Check a long response, switch voice during it (only the next response should
change), cancel/CANCELLED and a subsequent command. PCM/TCP/ESP firmware are
unchanged. Numerical samples and offline tests do not establish pronunciation
quality or a hardware pass.

For full rollback, stop the server and restore server.py, cancel_control.py,
local_config.py and the original local .env from `backups/before-silero-v5.6`.
Restore .env.example/tests/docs there as needed. New helper modules are inert
when the restored server does not import them. V4 weights remain unchanged.
Do not publish filled configs, cached installers/tools, weights, raw logs or
benchmark artifacts. No Git push was performed.

Activation backup: `backups/before-activate-v5.6` holds the V4-selected .env,
configuration defaults, tests and docs from immediately before this activation.
Restoring that .env selects V4; restoring its local_config.py also restores the
V4 default. The original earlier patch rollback above still applies if needed.
CPU benchmark summary and verification limits are recorded in STATUS.md. Private raw benchmark/cache files are not published.

Streaming is now accepted, enabled/configurable via the four LLM settings inREADME.
This supersedes any historical pending/defaultfalse text above; false remains the
full-response fallback. TTS model/voice parameters themselves remain unchanged.
