# Phase 2 public release review

Milestone: accepted Phase 2 New TTS (Silero V5.6 CPU). Target branch main;
commit title `feat: Phase 2 - New TTS (Silero V5.6)`; annotated tag
`v0.2.0-tts-stable`. Phase 3 streaming is next and not implemented.

Only the current server/config/voice/normalizer source, necessary tests, safe
.env.example and relevant documentation are included. MANIFEST.txt lists every
tracked file, not only this update. ESP firmware/build configuration and pinned
dependencies are unchanged from the phase1 commit. Original working projects and
ignored local configurations are not overwritten. Existing Git history remains.

Validation: full 165-test offline regression suite; AST/config and documented
local-model layout checks; staged file list and full diff inspection; known local
secret matching without printing values; credential/private-key patterns,
private path/account and binary/size checks; tracked Git blob/history checks;
root/nested ignore probes. These scans are independent of .gitignore. Automated
scanning is not an absolute guarantee. Detailed outcomes are in STATUS.md.

Excluded: v4_ru.pt/v5_6_ru.pt and other model weights, model_ru, virtual envs,
WAV/reference audio, logs, backups, caches, local .env files, Wi-Fi config headers,
generated sdkconfig, firmware binaries and build artifacts. Neither weights nor
filled config files are committed, even temporarily. Test credentials are synthetic.
No additional package installation, physical flashing or live paid API call is
required for this publication. Existing weights and private configs are retained.

The owner reports completion/acceptance of Phase 2. Prior CPU model checks and
software loopback are recorded in STATUS.md; no new hardware test is invented.
README explains deployment using locally supplied trusted v5_6_ru.pt and model-only
V4 rollback when the corresponding local V4 package is available. Historical
baseline paragraphs are retained as evidence, with current status taking priority.
