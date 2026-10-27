# CLAUDE.md

You are probably working on **someone else's** PC and TV: setting up Karaoke Party (via `PROMPT.md`), fixing it, or
customizing it. The person may not be technical. Explain briefly what you do and why, verify on the real devices,
and ask before anything that changes their system beyond this repo.

## Read first

1. `SPEC.md`: how everything works and why (section 11 is the decision log). Read it before changing behavior.
2. `README.md`: what the user sees, setup and troubleshooting.
3. `.env.example`: every setting. The user's `.env` holds their PIN and API key: never print them unmasked, never
   commit them.
4. For setup: `PROMPT.md` is the step-by-step plan; `scripts/install.ps1`, `scripts/install.sh` and
   `scripts/tv_setup.py` do most of the work. For removal: `UNINSTALL.md`, `scripts/uninstall.ps1`,
   `scripts/uninstall.sh`, `scripts/tv_uninstall.py`.
5. `data/install-manifest.json` (if it exists): what the installers changed on this PC and TV. Anything you install
   or change for the user (a tool, a rule, a TV setting) goes in there too (`server/install_manifest.py`), so the
   uninstaller can undo it; never remove something that isn't in it or clearly ours.

Map: `server/` (FastAPI app, worker, pipeline, SQLite), `web/` (phone + host pages, vanilla JS, no build step),
`tvapp/` (Android TV app, Kotlin, classic Views; colors/sizes/timings in `Theme.kt`), `scripts/`, `tests/`,
`docs/images/` (README images, generated), `data/` (runtime: db, stems, renders, lyrics, logs; gitignored).

## Safety rules

- **The music folder (`MUSIC_PATH`) is read-only.** Never write, move, rename or delete anything in it, not even
  "temporary" files. Everything generated goes to `data/`.
- **Ask before anything that needs admin rights:** UAC/sudo, firewall rules, scheduled tasks/autostart, `loginctl
  enable-linger`, system packages, drivers, changing the Windows network profile. Explain what it does first.
- **Ask before anything that depends on the user's preferences:** autostart, YouTube key, which songs to test with,
  deleting anything outside `data/`.
- **The TV is a household device.** Read-only commands are fine (`getprop`, `dumpsys`, `pm list`, `settings get`,
  screenshots, `logcat`, `input keyevent`, `am start` of our app). The **only** settings you may change without
  asking are the two grants for the Karaoke Party app itself (notification listener, and "display over other apps"
  on Android 10+), keeping existing entries. Ask before any other `settings put`.
- **Never disable, uninstall or clear system packages or other apps** on the TV (`pm disable*`, `pm uninstall`,
  `pm clear`). Uninstall only our own app (`io.github.maxlindnerhtl.karaokeparty`), and only after asking.
- Never download audio/video from YouTube or anywhere else, and don't add music or lyrics to the repo. Tests, docs
  and screenshots use invented songs only (`scripts/demo_data.py`).
- The installers and uninstallers ask on the terminal, which you can't answer: ask the user in chat, then run them
  with `-Yes`/`--yes` and the `-Keep*`/`--keep-*` or `-SkipAdmin`/`--no-service` flags that match the answers.
- Only touch tasks, rules, services and apps by their exact names (`Karaoke Party server`, `KaraokeParty-Server-TCP`,
  `KaraokeParty-Discovery-UDP`, `karaoke-party.service`, `io.github.maxlindnerhtl.karaokeparty`), never by a pattern
  like `Karaoke*`: the user may have other things with similar names.
- Don't expose the server beyond the LAN (no port forwarding, tunnels or `0.0.0.0` firewall rules for Public
  networks).

## Known gotchas

**PC**
- **ffmpeg needs the rubberband filter** (key changes). Many builds lack it, sometimes the one on PATH: check with
  `ffmpeg -hide_banner -filters`. The server checks at startup and prints how to fix it. `FFMPEG_PATH` points to a
  specific build. No ffprobe is needed.
- Demucs and WhisperX would shell out to whatever ffmpeg is on PATH: the pipeline always decodes with the configured
  ffmpeg and hands them arrays. Keep it that way.
- **uv isn't on PATH in shells that were already open** when it was installed: `~/.local/bin/uv` (Windows:
  `%USERPROFILE%\.local\bin\uv.exe`).
- **GPU:** torch 2.8 cu128 needs NVIDIA driver 570+ (RTX 50-series needs cu128 anyway). Without CUDA everything runs on
  the CPU with int8 Whisper `medium` (`DEVICE`, `WHISPER_MODEL` in `.env`); the host page shows a warning.
- The first worker start downloads ~4 GB of models (Hugging Face + torch caches in the user's home). On Windows
  without Developer Mode the HF cache warns about symlinks: harmless. pyannote's torchcodec warning is filtered and
  harmless.
- **Windows network profile Public** = the firewall rules (Private only) don't apply and the TV can't connect. If
  Windows asked to allow python.exe and someone pressed Cancel, that created a block rule that wins over ours.
- Only one server at a time (`port 8765 is in use`) and only one worker claims jobs (`data/worker.lock`). The tray app
  is Windows-only; on Linux it's the systemd user service. Under `pythonw` there is no console: logs are in
  `data/logs/`.
- LRCLIB's `/api/get` sometimes answers 503 for a miss; the code falls back to `/api/search`.
- YouTube Data API: ~95 searches per day on the free quota; results are cached for 7 days.

**TV**
- **Many TVs accept only one ADB connection at a time.** Disconnect other adb clients/tools first; a pending "Allow
  USB debugging?" prompt also blocks other connections. A new adb key always triggers that prompt: tell the user to
  accept it on the TV ("Always allow").
- Google TV / Android 11+ sets often have **Wireless debugging** only (pairing code, random port) instead of port
  5555; it switches itself off when Wi-Fi drops.
- **Notification-listener grant:** Android 8+ `cmd notification allow_listener <component>`; Android 7 has no such
  command, so append the component to `settings secure enabled_notification_listeners` with `:` and keep the existing
  entries. `scripts/tv_setup.py` does both and verifies.
- Android 10+ only lets a background app come back to the front with an exemption (bound notification listener, or
  `appops set <pkg> SYSTEM_ALERT_WINDOW allow`). Without both, YouTube songs still end on the timer, but the app
  stays behind YouTube until someone presses Back.
- Release and debug builds have different signatures: switching needs an uninstall (ask first). That drops the app's
  saved server address and Wake-on-LAN MAC, and the grants: rerun `scripts/tv_setup.py` after reinstalling.
- `adb install` can hang on some Android 14 TVs waiting for an on-screen confirmation: push to `/data/local/tmp/` and
  `pm install -r`. Run `pm` commands one per shell call.
- `am start -W` on slow TVs can report `Status: timeout` although the activity started; check
  `dumpsys activity activities | grep -i ResumedActivity`.
- Screenshots: `adb exec-out screencap -p > shot.png`. They are black/empty inside the YouTube app (secure surface).
- YouTube app after a cold start may show "Who's watching?" or a sign-in screen; the user picks a profile once.
  Pre-roll ads come before the video; the end detection accounts for them (SPEC section 9).
- On Windows Git Bash, `adb install` needs a Windows path (`C:\...`), not `/c/...`.

## Changing things

- Python via `uv`, Python 3.11, exact pins, `uv.lock` committed (Windows + Linux x86-64). Tests: `uv run pytest`
  (no GPU/network/ML packages needed). TV app: `cd tvapp && ./gradlew assembleDebug testDebugUnitTest lintDebug`
  (JDK 17, SDK platform 35).
- Changed behavior or decisions go into SPEC.md (and section 11, with the date) in the same commit.
- UI work: the TV app's look lives in `tvapp/.../Theme.kt` (colors, sizes, fonts, timings); the web pages' in
  `web/style.css` (CSS variables at the top). Verify TV changes with a screenshot from the real TV; regenerate README
  images with `scripts/readme_images.py` if the UI changed. The icon/banner come from `tvapp/art/build.py`.
- After changing render or loudness settings: `uv run python -m server.rerender` re-queues cached renders.
- Back up `data/karaoke.db` before schema changes (`data/backups/`).
- Don't commit `.env`, `data/`, keystores or `tvapp/keystore.properties`.
