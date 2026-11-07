# macOS DMG distribution

## Build

On an Apple Silicon Mac with the Python 3.12 runtime dependencies and
requirements-release.txt installed:

    DECISIONSAI_PYTHON="$HOME/.virtualenvs/decisions/bin/python" installer/build_app.sh

The interpreter above is a **build tool only**, not a customer dependency.
PyInstaller freezes Python, native extensions and resources into
`installer/release/DecisionsAI.app`. The builder exports committed HEAD into a
throwaway source directory, so neither dirty operator changes nor untracked
settings, credentials, downloaded models or caches enter the release. Commit
packaging edits before building. Build artifacts are ignored; never commit a DMG.
The Go sidecar is built and shipped if Go is available; otherwise the app warns.
The developer Dock wrapper is explicitly separate from this portable bundle.

The default build is ad-hoc signed and **not notarized**. It is a preview, not a
Gatekeeper-trusted release. For public trusted distribution Paul must provide a
valid Apple Developer ID Application certificate/private key in the Mac Keychain,
and a `notarytool` keychain profile configured with Apple credentials:

    DEVELOPER_ID_APPLICATION='Developer ID Application: ... (TEAMID)' \
    NOTARYTOOL_PROFILE='decisions-notary' \
    DECISIONSAI_PYTHON=... installer/build_app.sh --release

This signs with hardened runtime, notarizes the DMG, staples tickets and checks
Gatekeeper. No certificate/profile is assumed to exist. Test Developer-ID nested
native code and entitlements on a clean Mac before claiming production readiness.
Ad-hoc signing does not bypass Gatekeeper. Do not disable macOS security globally.

## Website and updates

The existing site is `/var/www/decisions.tensology.com` on the site's existing SSH
host (see deploy_changelog_to_site.sh). Only its `frontend` files are involved;
no nginx configuration or service restart is needed. Put the versioned DMG and
SHA256SUMS into `frontend/public/downloads/`, plus `latest-macos.json` with:

    {"version":"2.9.5","architecture":"arm64","url":"https://decisions.tensology.com/downloads/DecisionsAI-2.9.5-arm64.dmg","sha256":"<64 lowercase hex>","signing":"ad-hoc","notarized":false}

Run `python3 scripts/update_site_downloads.py /var/www/decisions.tensology.com`
then `cd frontend && npm run build`. The patcher is versioned here because the
website is a separate checkout with a different origin. Never push its bare
remote as part of this job. `install.sh` is unchanged, still a source/developer
fallback. Its command is under a collapsed developer section, not the primary UI.

On launch the bundled app checks the HTTPS manifest in a worker thread and offers
an **Open download page** notice for a newer stable version. Users download the
DMG, explicitly quit, replace the app and reopen. No downloaded code is executed
automatically. The checker validates host, architecture and checksum field but
only opens our fixed download page; it does not claim to verify/install the DMG.
This is the deliberately minimal update path, not Sparkle/automatic replacement.
User data remains in `~/Library/Application Support/DecisionsAI` across updates.

## Optional models and media

No Whisper/Vosk model is bundled. First launch prompts for Vosk small English
(~40 MB) or Whisper base.en (~148 MB), or **Not now**. The explicit Download button
shows byte progress; failed/cancelled downloads do not activate a model. Later,
the native **Speech engine** menu can change/download the engine or the optional
avatar/media pack. SHA-256 and size are checked before activation; Vosk archives
are path-validated before extraction. The mirror manifest lives at
`/downloads/models/manifest.json`, keys `vosk`, `whisper`, `optional_media`, each
with HTTPS `url`, `bytes` and `sha256`. Models live in Application Support under
`models/speech`; media goes under `assets`. Existing developer models are untouched.
No local model choice means the voice worker does not start.

Official Vosk model guidance: https://alphacephei.com/vosk/models — small models
are around 50 MB and about 300 MB runtime RAM; the English 0.15 model is 40 MB,
Apache 2.0. Streaming on modest CPUs is its advantage. Whisper supports more
languages and is generally preferable on difficult audio, with larger models and
more computation. This release offers **English defaults**, not a multilingual
accuracy guarantee. Accuracy was not benchmarked on Paul's speech in this job.
Official Whisper model source: https://huggingface.co/ggerganov/whisper.cpp .

The 5.3 GB local `distr/core/agent/models` directory was already ignored, not
tracked. Tracked tree measured 247,509,074 bytes before cleanup; optional assets
media accounted for 118,734,863 bytes / 171 files. These are removed **from the
index only** so the live checkout still has its media, and published separately.
Git pack size was 605.92 MiB, with 24.91 MiB pre-existing temporary pack garbage.
Largest reachable historical blob: `assets/img/oracle/0.gif`, 10,048,154 bytes.
Full clone history is not made smaller by current-tree removals. No history
rewrite, garbage collection or force-push is performed; coordinated explicit
approval would be required for a future history rewrite. Developer fallback
still downloads history, so it is not the recommended install.

## Safe verification

`Contents/MacOS/DecisionsAI --bundle-self-test` exits before environment bootstrap,
GUI, process cleanup or DB access, reporting frozen Python and Qt availability.
`--help` is similarly safe. A normal frozen launch refuses a used web port rather
than killing another instance. The installed smoke tool uses a temporary HOME,
DECISIONS_DATA_DIR and a separate port, but first-run dialogs require interactive
review. Do not kill/restart the live checkout to obtain a green smoke test.
