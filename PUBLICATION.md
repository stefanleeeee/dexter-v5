# Publication review — pending approval

Target: a new PUBLIC GitHub repository named dexter-v5. No public repository has been created and nothing has been pushed. Only the owner may authorize that final step. MANIFEST.txt lists the proposed files; Git uses a fresh history with the requested baseline commit message. No old history/remotes or private Git author identity are copied.

## Validation

- 145 offline tests pass in the original workspace and in this clean copy: 136 regressions + 9 configuration tests.
- Server/config syntax passes without importing models. Local configuration imports and validation tests run without credentials in fixtures or output. Working Python/ESP values compared equal before/after extraction.
- Existing working firmware rebuild and a full clean-copy ESP32-S3/ESP-IDF v6.1 build pass; app size 0xc9f00, 21% partition free. Clean generated sdkconfig settings match the tested original.
- No packages installed and no device flashed. New config extraction still needs a hardware startup smoke after the owner chooses to flash.
- Local SDK note: its constraints file was absent; the clean build used the installed tools with IDF_PYTHON_CHECK_CONSTRAINTS=no, ESP_IDF_VERSION=6.1 and PYTHONUTF8=1. Package constraints were not downloaded. This does not establish a fresh SDK installation test on another machine.
- Original tests need DEXTER_ESP_SOURCE for the separate ESP tree; the public copy defaults to its esp32 source directory. clang path is configured by DEXTER_ESP_CLANG or PATH; no workstation path is embedded.
- Public requirements.txt is UTF-8, with unchanged package pins. No clean network installation was attempted.

## Exclusions and audit

Only allowlisted text files are staged. Filled .env and dexter_local_config.h (including nested paths), generated sdkconfig, build/binaries, .venv, old Git history, backups, editor configs, model weights, recordings/references, caches, logs and raw environment baseline are excluded. Public configuration examples contain dummy values. README/docs contain generic paths, not the owner's workstation/IP/account details.

Review checks include full staged file list/diff, matching known original secret values without printing them, credential/private-key patterns, personal-path/account patterns, sizes/binary detection, ignore tests at root and nested paths, and scanning committed blobs. No dedicated gitleaks/trufflehog tool is installed; none was installed. No automated scan is an absolute guarantee. Do not publish compiled firmware: it contains credentials even though its C source is clean. Existing credentials are not rotated automatically.

The original Python tree had no Git repository. The ESP tree had one initial commit and local modifications; its existing origin/history are preserved outside this repository. Credential-pattern scanning of that available initial history did not find matches; old source copies/backups do contain credentials and remain private. Original history is deliberately not imported.

## Manual smoke

Use the original working configuration, launch server with the explicit project interpreter, build/flash only when ready, confirm ESP joins, one short DONE, one long answer, cancel/CANCELLED, next command and reconnect. Never share raw logs until reviewed for user speech, private addresses or API details. No automatic credential rotation or CosyVoice work is part of this publication.

## Final pre-commit checks

Twenty root/nested ignore probes passed; safe config examples remain included. Staged allowlist is 33 UTF-8 text files, with no filled configs, models or binary build artifacts. Known-value and credential-pattern scans, private path/account checks and staged-diff review passed. Source files retain some pre-existing trailing spaces; no cosmetic rewrite was performed. Full network installation and post-extraction hardware voice smoke were not performed.
