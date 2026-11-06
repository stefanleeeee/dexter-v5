# Phase 4 stable publication review

Accepted milestone: Contextual Voice Dialogue. Existing main; one new commit
`feat: Phase 4 - Contextual Voice Dialogue`; annotated tag
`v0.4.0-dialogue-stable`. Parent is phase3 commit
`75ddf62234a871f35c3cb93c855dc430663ee37d`. Existing tags/history are preserved.
Phases1–4 complete/accepted; Phase5 Windows control is next and not implemented.

Owner acceptance covers fresh capture requested/confirmed, follow-up open/accepted,
successful short contextual follow-ups, playback DONE, window expiry and six-turn
history bound. Other acoustic/Reset/long-session scenarios are not invented.
No new hardware flash or Groq calls are made for publication.

Publish only Python source/tests, documentation, safe templates, ESP source and
existing safe sdkconfig.defaults. Preserve deployment instructions and local model
provisioning. Keep filled .env/Wi-Fi headers, generated sdkconfig, models/weights,
reference/audio, logs, build outputs, backups, virtual environments and caches out.
MANIFEST.txt lists the complete proposed tree. Local configs are not overwritten.

304 regression tests passed against the public source and its own ESP source;
ESP-IDF bootloader+app build target passed, app0xca470 with21% app space free.
No clean dependency installation is claimed. No new runtime/dependency changes
are introduced just for publication. See STATUS/README for exact evidence/limits.

Inspect staged list/full diff, known local secrets without revealing values,
token/key patterns, personal data/private paths, binary/reference content and all
Git objects. Ignore rules supplement, not replace, content inspection. Audit the
actual commit and annotated tag before push. Scanning is not an absolute guarantee.

Remote main must still equal the parent above; stop on unexpected changes or tag
conflict. Push only main and this tag, never force. Verify public GitHub main/tag
and every published blob against the audited commit. Reports/backups stay outside
this repository. No Phase5 work is authorized by this release.
