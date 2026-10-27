# The setup prompt

Paste everything below the line into [Claude Code](https://claude.com/claude-code) on the PC that holds your music
(Windows 10/11 or Linux). The PC and the TV must be on the same network. It works with Android TV and Google TV sets
on Android 7.0 or newer: Sony, Philips, TCL, Hisense, Xiaomi, Chromecast with Google TV, Google TV Streamer, Nvidia
Shield and most others. It does **not** work on Samsung (Tizen), LG (webOS), Roku or Apple TV; the prompt checks
this and stops.

**Before you start, on the TV:** Settings → Device Preferences → About → press OK on **Build** 7 times until "You are
now a developer" appears. Go back → **Developer options** → turn on **USB debugging**, and **Network debugging** /
**ADB debugging** if your TV shows it. Google TV sets often only have **Wireless debugging**: turn that on instead.
Keep the TV on and on the same Wi-Fi as the PC.

Claude Code asks before anything that needs admin rights or depends on your preferences, and tells you when to
accept a prompt on the TV. Plan for about 30-60 minutes; most of it is downloads.

---

I want to set up Karaoke Party (https://github.com/maxlindnerhtl/karaoke-party) on this PC and my Android TV: a LAN
karaoke system where my PC removes the vocals from songs in my music folder and syncs the lyrics, guests queue songs
from their phones, and a TV app shows the lyrics. Work on the real devices, step by step, verify each step, and keep
me posted in short messages. Ask me before anything that needs admin rights (UAC/sudo, firewall, autostart,
system-wide installs) or that depends on my preferences.

Get the repo and the rules
- If the current folder isn't a clone of the repo, ask me where to put it, then `git clone` it there.
- Read `CLAUDE.md` (rules and known gotchas for agents), then `SPEC.md` (how everything works) and `README.md`. Follow
  CLAUDE.md's safety rules throughout: my music folder is read-only, never disable or uninstall anything on the TV,
  and the only TV setting you may change is the notification-listener grant (plus "display over other apps") for
  the Karaoke Party app itself.

Check the PC
- Report OS and version, CPU, RAM, free disk space (need ~15 GB plus ~200 MB per song), and the GPU:
  `nvidia-smi` shows an NVIDIA GPU and its driver. CUDA 12.8 needs driver 570 or newer. If there is no NVIDIA GPU,
  tell me that processing will run on the CPU (it works, but takes several minutes per song) and ask whether to
  continue. If the driver is too old, tell me how to update it; don't install drivers yourself.
- Check git, uv, ffmpeg and adb. ffmpeg must have the rubberband filter: `ffmpeg -hide_banner -filters` must list
  `rubberband`. On Windows the installer downloads a suitable ffmpeg itself; on Linux, if the distribution's ffmpeg
  lacks it, tell me the package to install (that needs sudo: ask first). adb comes from Android SDK Platform-Tools:
  if it's missing, offer to download the official zip to a folder in my user directory (no admin needed) and use it
  from there.

Configure and install
- Ask me for my music folder. Check it exists and how many audio files (mp3, m4a, flac, ogg, wav) it has; never
  write, move or delete anything in it. If it holds DRM-protected files (e.g. old iTunes .m4p), tell me they can't be
  used.
- Ask me to pick a host PIN (for the host page), and whether I have a YouTube Data API v3 key for songs that aren't
  in my library (optional; if I want one, explain briefly how to create it in the Google Cloud console).
- Run the installer: `powershell -ExecutionPolicy Bypass -File scripts\install.ps1` on Windows, `scripts/install.sh`
  on Linux. It creates `.env` from `.env.example`: fill in `MUSIC_PATH`, `HOST_PIN` and `YT_API_KEY` yourself and show
  me the result with the PIN and key masked. Before its admin step, explain what it will do (firewall rules for TCP
  8765 and UDP 8766 on Private networks only, autostart at logon on Windows; ufw/firewalld rules and
  `loginctl enable-linger` on Linux) and ask me. If I say no, explain how to start the server by hand instead.
- Windows: check the network profile of the active connection (`Get-NetConnectionProfile`). If it is Public, the
  firewall rules don't apply and the TV can't reach the server: ask me before switching it to Private (admin).
- Confirm torch sees the GPU (`uv run python -c "import torch; print(torch.cuda.is_available())"`), or that the CPU
  fallback is what I chose.

Start the server
- Start it: the tray app on Windows (`.venv\Scripts\pythonw.exe -m server.tray`), the systemd user service on Linux
  (`systemctl --user start karaoke-party`), or `uv run python -m server` if I declined autostart. Only one server
  may run: if port 8765 is taken, find out what holds it and ask me before stopping anything.
- Check `http://127.0.0.1:8765/api/queue` answers, find the PC's LAN IP, and ask me to open `http://<PC IP>:8765/`
  on my phone (same Wi-Fi). If it doesn't load, go through the firewall/network-profile checks in README
  "Troubleshooting". Show me the first lines of `data/logs/server.log` and `worker.log`; the first worker start
  downloads ~4 GB of models in the background.

Connect to the TV
- Ask me the TV's brand and model. If it is a Samsung (Tizen), LG (webOS), Roku, Apple TV or another TV that isn't
  Android TV / Google TV, stop and explain that the TV app needs Android TV; suggest an Android TV stick (e.g. a Google
  TV Streamer or Chromecast with Google TV) on an HDMI port as the way to use it. Fire TV runs Fire OS (Android-based):
  say it's untested and ask whether to try.
- Find the TV's IP: ask me (TV Settings → Network & Internet → the network), or look in the ARP table / `adb mdns
  services` for candidates and confirm with me. Ask before scanning the network for open ports.
- Connect: `adb connect <TV IP>:5555`. Tell me right before it that **"Allow USB debugging?" will appear on the TV**
  and that I should tick "Always allow" and press OK; wait for me. Many TVs accept only one ADB connection at a time:
  if it times out, check nothing else is connected (`adb disconnect`).
- If port 5555 refuses: the TV may only offer Wireless debugging. Ask me to open Developer options → Wireless
  debugging → **Pair device with pairing code** and read you the IP:port and code, then `adb pair <ip>:<pair port>
  <code>` and `adb connect <ip>:<port shown under Wireless debugging>`. Android switches Wireless debugging off when
  Wi-Fi drops, so do the TV steps in one go. If neither works, offer USB: a USB-A to USB-A cable is rarely an option
  on TVs, so explain and let me decide.
- Read and report: `getprop ro.product.manufacturer`, `ro.product.model`, `ro.build.version.release`,
  `ro.build.version.sdk`, the screen size (`wm size`), and whether a YouTube app is installed (`pm list packages
  youtube`). The app needs API 24 (Android 7.0) or newer: if the TV is older, stop and explain. A missing YouTube app
  only disables YouTube fallback songs: say so and continue.

Install the TV app
- Download the latest release APK and its `.sha256` from https://github.com/maxlindnerhtl/karaoke-party/releases
  (`gh release download` or the release URLs), verify the checksum, and run
  `python scripts/tv_setup.py --tv <TV IP[:port]> --apk <apk>` (or `--serial <serial>` for a paired/USB device). It
  installs the app, grants the notification listener the right way for the Android version (Android 8+:
  `cmd notification allow_listener`; Android 7: appends to `enabled_notification_listeners`, keeping the existing
  entries) and on Android 10+ allows "display over other apps" for the app. Explain these two grants to me in one
  sentence each before running it (they let YouTube songs return to the karaoke app when they end).
- If `INSTALL_FAILED_UPDATE_INCOMPATIBLE`: a build with another signature is installed; ask me before uninstalling it.
  If `adb install` hangs (some Android 14 TVs wait for an on-screen confirmation), push the APK to /data/local/tmp/
  and run `pm install -r` over the shell instead.
- If no release exists yet, build it: `cd tvapp && ./gradlew assembleRelease` needs JDK 17 and the Android SDK
  (platform 35). Ask me before installing them, and use `assembleDebug` if there's no signing key.

Verify
- Start the app (`am start -n io.github.maxlindnerhtl.karaokeparty/com.karaoke.tv.MainActivity`), wait 5 s and take a
  screenshot: `adb exec-out screencap -p > shots/lobby.png`. Look at it: the lobby must show the QR code with the
  PC's address and no "YouTube end detection is off" hint. If it says "Server offline", fix the network/firewall
  first (README "Troubleshooting"). If a screenshot comes back empty, check
  `dumpsys activity activities | grep -i ResumedActivity` instead.
- Ask me to scan the QR code, enter a name and queue one song from my library. Watch `data/logs/worker.log` and the
  host page until it's ready (about a minute on a GPU, several on the CPU, longer for the very first song while the
  models download), then take a screenshot of the lyrics on the TV. If I have a YouTube key, have me queue a song
  that isn't in my library too and check it returns to the karaoke app when it ends (`adb logcat -s Karaoke`).
- Optionally (ask me): open the YouTube app on the TV once so I can sign in or pick "watch as guest" and a profile;
  otherwise its profile picker interrupts the first YouTube song.

Finish
- Summarize what was installed and changed: on the PC (folders, firewall rules, autostart, network profile), on the
  TV (the app and its two grants, nothing else), and where the logs are. Tell me the phone URL, the host page URL
  and that the PIN is in `.env`.
- Tell me how to switch ADB off again, now that it's no longer needed: on the TV, Developer options → USB debugging
  off, Network/ADB debugging or Wireless debugging off, and "Revoke USB debugging authorizations"; then
  `adb disconnect`. The grants stay in place. To update the app later, switch debugging on again and rerun
  `scripts/tv_setup.py` with the new APK.
- Point me to the "Customize it with Claude Code" section of the README.
