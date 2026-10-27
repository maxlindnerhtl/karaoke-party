# CLAUDE.md

Guide for coding agents (and humans) working on this repo. `SPEC.md` is the source of truth: read it before changing
behavior. `README.md` is the user-facing setup guide.

## Conventions

- **Python via `uv`**, Python 3.11. Pin exact versions (`==`) and commit `uv.lock` (it covers Windows and Linux
  x86-64). No global pip installs.
- Changed decisions go into SPEC.md section 11 (Decision log) with the date, in the same commit as the change.
- Secrets and machine config live in `.env` (gitignored). `.env.example` lists the keys and is committed. Never commit
  `.env`, `data/`, keystores or `tvapp/keystore.properties`.
- `MUSIC_PATH` is read-only: never write, move or delete anything in it.
- Ask before anything that needs admin rights (firewall rules, scheduled tasks, linger, system packages) and before
  deleting files outside `data/`.
- No real song titles, artists or lyrics in tests, fixtures, docs or screenshots: use invented ones
  (`scripts/demo_data.py`).

## Running

- Server + worker: `uv run python -m server` (phone page `http://<PC IP>:8765/`, host page `/host`, debug page
  `/debug`). API only: `--no-worker`; worker alone: `uv run python -m server.worker`. Logs: `data/logs/`.
- Windows tray: `uv run python -m server.tray` (autostart runs `.venv\Scripts\pythonw.exe -m server.tray`).
  Linux: `systemctl --user start karaoke-party` (from `scripts/install.sh`).
- Only one server runs at a time (port check) and only one worker claims jobs (`data/worker.lock`).
- Tests: `uv run pytest` (no GPU, network or ML packages needed; CI uses `uv sync --no-group ml`).
- Demo data without real music: see the docstring of `scripts/demo_data.py`.
- After changing render or loudness settings: `uv run python -m server.rerender` (re-queues every cached render).

## TV app

- Build: `cd tvapp && ./gradlew assembleDebug testDebugUnitTest lintDebug` (JDK 17, Android SDK with platform 35;
  set `JAVA_HOME` and `sdk.dir` in `tvapp/local.properties` or `ANDROID_HOME`).
- Instrumented tests: `./gradlew connectedDebugAndroidTest` on a device/emulator after
  `python scripts/tv_setup.py --serial <device> --grant-only` (CI runs them on Android TV emulators API 24/30/34).
- Install + grants on a TV: `python scripts/tv_setup.py --tv <TV IP> --apk <apk>` (plain adb). Logs:
  `adb logcat -s Karaoke`.
- Many TVs accept only one ADB connection at a time: `adb disconnect` other tools first. A new adb key makes the TV
  ask "Allow USB debugging?"; someone has to accept it on the TV.
- Release signing comes from `KARAOKE_KEYSTORE*` environment variables (CI secrets) or `tvapp/keystore.properties`.
- Verify UI changes on a real TV or emulator with `adb exec-out screencap -p > shot.png`.
- Icon/banner: edit `tvapp/art/build.py`, never the generated files.
