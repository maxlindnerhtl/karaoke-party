# The uninstall prompt

Paste everything below the line into [Claude Code](https://claude.com/claude-code), opened in your Karaoke Party
folder on the PC. It removes Karaoke Party from the PC and the TV and puts both back the way they were before the
install. It lists everything first and asks before it changes anything; your music folder is never touched.

**On the TV, before you start:** debugging must still be on (Developer options → USB debugging, and Network/ADB or
Wireless debugging), so Claude Code can remove the app. You'll switch it off at the end.

---

I want to remove Karaoke Party from this PC and my Android TV, and put both back the way they were before the
install. This folder is the installation. Follow CLAUDE.md's safety rules: never touch my music folder (`MUSIC_PATH`
in `.env`), change nothing on the TV except removing the Karaoke Party app and its own grants, and ask me before
anything that needs admin rights.

List first, change nothing yet
- Read `data/install-manifest.json` (what the installers recorded). If it doesn't exist, this is an install from
  before manifests: say so, and work from what you detect.
- Show me one list of everything that will be removed or restored, with sizes:
  - PC: running server/worker/tray processes of this installation, the autostart task `Karaoke Party server`
    (Windows) or the `karaoke-party` systemd user service (Linux), the firewall rules `KaraokeParty-Server-TCP` and
    `KaraokeParty-Discovery-UDP` (or the ufw rules from the manifest), the network profile change if the installer
    made one, `.venv`, the data folder (database, processed songs, logs), the model downloads listed in the manifest,
    and the tools the installer added (only those marked `installed_by_us`; never tools I had before).
  - TV: the app `io.github.maxlindnerhtl.karaokeparty`, its notification-listener entry and (Android 10+) its
    "display over other apps" grant. Read the current `enabled_notification_listeners` value and show me which
    other apps' entries will stay.
  - What stays: my music folder, `.env` and the code in this folder (I can delete the folder myself afterwards).
- Ask me which items to keep (data, tools, network profile) and whether to go ahead.

Export my stats (optional)
- Before anything is deleted, offer to export the party stats: `python scripts/export_stats.py` writes most sung
  songs, top singers and the play history to a JSON + CSV file in my home folder. Tell me where it is.

The TV first (it needs the manifest's TV record, which lives in the data folder)
- Connect over adb like the setup did (tell me before "Allow USB debugging?" appears). Then run
  `python scripts/tv_uninstall.py --tv <TV IP[:port]>` (or `--serial`). It revokes the app's appops grant, uninstalls
  the app, checks that its listener entry is gone (Android removes it with the app; the script removes only ours if
  it's still there) and verifies that all other listeners are still present. Show me its output and the listener
  value before and after.
- Take a screenshot of the TV's home screen (`adb exec-out screencap -p > shots/after-uninstall.png`) and confirm the
  Karaoke Party tile is gone.

Then the PC
- Windows: `powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Yes` plus `-KeepData`, `-KeepTools`,
  `-KeepNetworkProfile` for what I want to keep. It stops the server, removes the task and firewall rules in one
  elevated step (tell me a UAC prompt is coming), and deletes the folders and tools I agreed to.
  Linux: `scripts/uninstall.sh --yes` plus `--keep-data`, `--keep-tools`, `--keep-linger` (sudo may ask for my
  password for the ufw rules and linger).
- Verify: no processes from this folder's `.venv` running; the task/service and both firewall rules are gone
  (`Get-ScheduledTask`, `Get-NetFirewallRule -Name KaraokeParty-*`, or `systemctl --user status karaoke-party`,
  `sudo ufw status`); the deleted folders and model downloads no longer exist; the network profile is what it was.
  Check that nothing else with similar names was touched (other tasks, rules or apps that just start with "Karaoke").

Switch ADB off on the TV
- Guide me through it on the TV, one step at a time: Settings → Device Preferences → Developer options → turn off
  **USB debugging**, **Network debugging** / **ADB debugging** and **Wireless debugging** (whichever the TV has), then
  select **Revoke USB debugging authorizations**. Optionally turn **Developer options** off entirely. Then restart the
  TV (some TVs keep port 5555 open until a restart).
- Run `adb disconnect`, then `python scripts/tv_uninstall.py --check-adb-closed <TV IP>`: it must report that port
  5555 **refuses** the connection. "No answer" proves nothing (TV off or asleep): check again with the TV on. If it's
  still open, go through the Developer options again with me.

Finish
- Summarize what was removed and restored on the PC and the TV, what was kept, where the stats export is (if any),
  and that the repository folder can now be deleted by hand.
