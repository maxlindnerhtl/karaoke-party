package com.karaoke.tv

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.util.Log
import android.util.TypedValue
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import com.karaoke.tv.Theme.Colors
import com.karaoke.tv.Theme.Space
import com.karaoke.tv.Theme.Text
import com.karaoke.tv.Theme.Timing
import com.karaoke.tv.yt.YouTubeApps
import com.karaoke.tv.yt.YtWatchService
import org.json.JSONObject
import kotlin.math.ceil

/**
 * The whole TV app (SPEC section 9): lobby, lyrics player, stats and offline screens in one Activity, with the
 * reactions overlay on top. The server owns the queue; this app asks it for the next entry, plays it (library:
 * ExoPlayer + lyrics; YouTube: the YouTube app, watched by [YtWatchService]) and reports playback events back.
 */
class MainActivity : Activity(), ServerClient.Listener {
    private lateinit var client: ServerClient
    private lateinit var player: ExoPlayer
    private lateinit var lobby: LobbyView
    private lateinit var playerLayer: FrameLayout
    private lateinit var lyricsView: LyricsView
    private lateinit var singersText: TextView
    private lateinit var titleText: TextView
    private lateinit var nextLine: TextView
    private lateinit var offlineNote: TextView
    private lateinit var stats: StatsView
    private lateinit var reactions: ReactionsView
    private lateinit var toast: TextView
    private lateinit var offlineLayer: LinearLayout
    private lateinit var offlineTitle: TextView
    private lateinit var offlineDetail: TextView
    private lateinit var wolButton: Button
    private val handler = Handler(Looper.getMainLooper())

    private var queue: QueueState? = null
    private var connected = false
    private var current: Entry? = null        // library entry loaded in the player (playing or paused)
    private var pendingYt: Entry? = null      // YouTube entry the server says is playing, but nothing watches it (app restarted)
    private var pendingStats: JSONObject? = null   // show_stats that arrived during a song: shown when it ends
    private var level = 0
    private var levelsReady: List<Int> = emptyList()
    private var lyrics: Lyrics? = null
    private var showingPlayer = false
    private var startedReported = false
    private var confirmSkip = false
    private var countdownEnd = 0L
    private var countdownCancelled = false
    private var starting = false
    private var errors = 0
    private var waitingForServer = false   // playback stopped with a stream error while the server was down
    private var lastPositionReport = 0L
    private var queueRefreshPending = false
    private var offsetPending = false
    private val sendOffset = Runnable { postOffset() }
    private val hideToast = Runnable { toast.visibility = View.GONE }
    private val hideStats = Runnable { closeStats() }

    private val tick = object : Runnable {
        override fun run() {
            val now = SystemClock.elapsedRealtime()
            if (current != null && player.isPlaying && now - lastPositionReport >= 1000) {
                lastPositionReport = now
                report("position")
            }
            if (!showingPlayer) updateLobby()
            handler.postDelayed(this, 250)
        }
    }

    // --- lifecycle -------------------------------------------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(buildViews())
        player = ExoPlayer.Builder(this).build().apply { addListener(playerListener) }
        lyricsView.timeMs = { player.currentPosition }
        lyricsView.durationMs = { player.duration }   // C.TIME_UNSET (negative) until known: no progress bar
        client = ServerClient(this, this)
    }

    override fun onStart() {
        super.onStart()
        client.start()
        handler.post(tick)
    }

    override fun onResume() {
        super.onResume()
        // back from YouTube before the watcher noticed the end (Back in the YouTube app): continue the queue
        if (YtWatchService.active != null) YtWatchService.returned(this)
        lobby.setWarning(if (YtWatchService.listenerEnabled(this)) null
            else "YouTube end detection is off, so YouTube songs return after a timer. To turn it on, run from a PC: " +
                YtWatchService.grantCommands(this).joinToString("  ·  ") + "  (see the README)")
    }

    override fun onStop() {
        handler.removeCallbacks(tick)
        if (player.playWhenReady) player.pause()
        if (showingPlayer) showLobby()
        client.stop()
        connected = false
        super.onStop()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        player.release()
        super.onDestroy()
    }

    // --- server events ---------------------------------------------------------------------------------------

    override fun onConnected(base: String) {
        connected = true
        lobby.setServer(base)
        offlineLayer.visibility = View.GONE
        offlineNote.visibility = View.GONE
        EndReports.flush(this, base)   // songs that ended while the server was down
        if (waitingForServer && current != null) {   // the stream broke while offline: continue where it stopped
            waitingForServer = false
            errors = 0
            showToast("Server is back, continuing…")
            player.prepare()
        }
        // remember the PC's MAC for Wake-on-LAN from the offline screen
        Http.get("$base/api/server_info") { code, json ->
            if (code != 200 || json == null) return@get
            val e = Http.prefs(this).edit()
            json.optStringOrNull("mac")?.let { e.putString("mac", it) }
            json.optStringOrNull("ip")?.let { e.putString("server_ip", it) }
            e.apply()
        }
    }

    override fun onOffline(detail: String, retryInSec: Int) {   // retryInSec: for logging; the text must not go stale
        connected = false
        offlineTitle.text = "Server offline"
        offlineDetail.text = "$detail. Retrying automatically…"
        offlineNote.visibility = View.VISIBLE
        if (!showingPlayer) showOffline()
    }

    override fun onEvent(msg: JSONObject) {
        when (msg.optString("type")) {
            "hello", "queue_changed" -> applyQueue(QueueState.from(msg))
            "job_progress" -> refreshQueueSoon()
            "skip" -> {
                val id = msg.optInt("entry_id", -1)
                if (id == current?.id) stopCurrent("Skipped by the host")
                if (id == pendingYt?.id) pendingYt = null
            }
            "offset_changed" -> lyrics?.let { l ->
                if (msg.optString("song_id") == current?.songId && !offsetPending) l.offsetMs = msg.optInt("offset_ms")
            }
            "settings_changed" -> msg.optJSONObject("settings")?.let { st ->
                lyrics?.globalOffsetMs = st.optInt("global_offset_ms", lyrics?.globalOffsetMs ?: 0)
            }
            "reaction" -> reactions.add(msg.optString("emoji"), msg.optString("name"))
            "show_stats" -> if (showingPlayer) {
                pendingStats = msg                    // don't cover the lyrics: show it when the song ends
                showToast("Stats will show after this song")
            } else showStats(msg)
        }
    }

    private fun applyQueue(q: QueueState) {
        queue = q
        val p = q.playing
        val cur = current
        if (cur != null && p?.id == cur.id) levelsReady = p.levelsReady
        if (cur != null && startedReported && p?.id != cur.id) stopCurrent("Song ended on the server")
        if (p == null) pendingYt = null
        // the server still has an entry playing that this app doesn't (the app was restarted): offer to continue it
        if (p != null && current == null && pendingYt == null && !starting && YtWatchService.active == null &&
            p.id !in YtWatchService.finished && !EndReports.isPending(this, p.id)) {
            if (p.isYoutube) pendingYt = p else loadLibrary(p, (q.playbackPosition * 1000).toLong(), play = false)
        }
        lobby.setQueue(q.upcoming)
        nextLine.text = nextLineText(q.upcoming.filter { it.id != current?.id }.take(Space.NEXT_UP_ENTRIES))
        if (!showingPlayer) updateLobby()
    }

    /** Job progress changes don't send queue_changed; refresh the lobby's percentages at most once a second. */
    private fun refreshQueueSoon() {
        if (queueRefreshPending || showingPlayer) return
        queueRefreshPending = true
        handler.postDelayed({
            queueRefreshPending = false
            client.base?.let { b -> Http.get("$b/api/queue") { code, json -> if (code == 200 && json != null) applyQueue(QueueState.from(json)) } }
        }, 1000)
    }

    // --- starting entries ------------------------------------------------------------------------------------

    private fun startNext() {
        val base = client.base ?: return
        if (starting || !connected) return
        starting = true
        countdownEnd = 0
        lobby.setBanner("Starting…")
        Http.post("$base/api/player/next") { code, json ->
            starting = false
            val e = json?.optJSONObject("entry")?.let(Entry::from)
            when {
                code != 200 -> showToast("Server error ($code)")
                e == null -> showToast("Nothing is ready yet")
                e.isYoutube -> playYoutube(e)
                else -> loadLibrary(e, 0, play = true)
            }
        }
    }

    private fun loadLibrary(e: Entry, positionMs: Long, play: Boolean) {
        val base = client.base ?: return
        current = e
        level = e.vocalLevel
        levelsReady = e.levelsReady
        startedReported = !play   // resuming after an app restart: the server already has it as started
        errors = 0
        waitingForServer = false
        lyrics = null
        lyricsView.lyrics = null
        lyricsView.duet = if (e.singers.size == 2) e.singers else null
        lyricsView.message = "Loading lyrics…"
        val songId = e.songId
        Http.get(base + (e.lyricsUrl ?: "/api/lyrics/$songId")) { code, json ->
            if (current?.id != e.id) return@get
            if (code == 200 && json != null) {
                val l = Lyrics.from(json)
                lyrics = l
                lyricsView.lyrics = l
                lyricsView.message = null
                updatePlayerOverlay(e)
            } else {
                lyricsView.message = "No lyrics for this song"
            }
        }
        player.setMediaItem(MediaItem.fromUri(base + (e.mediaUrl ?: e.mediaUrlFor(level))), positionMs)
        player.prepare()
        player.playWhenReady = play
        updatePlayerOverlay(e)
        if (play) showPlayer() else showLobby()
    }

    private fun playYoutube(e: Entry) {
        val base = client.base ?: return
        val app = YouTubeApps.launch(this, e.videoId ?: "")
        YtWatchService.start(this, e, app?.second)
        try {
            if (app == null) throw ActivityNotFoundException("no app opens YouTube links")
            Log.i(TAG, "yt: opening ${e.videoId} in ${app.second}")
            startActivity(app.first)
            Http.post("$base/api/player/started", JSONObject().put("entry_id", e.id).put("position", 0))
        } catch (ex: ActivityNotFoundException) {
            Log.e(TAG, "YouTube app missing", ex)
            YtWatchService.returned(this)   // reports skip (never seen) and stops the watch
            showToast("The YouTube app is not installed")
        }
    }

    // --- playback --------------------------------------------------------------------------------------------

    private val playerListener = object : Player.Listener {
        override fun onIsPlayingChanged(isPlaying: Boolean) {
            if (current == null) return
            if (isPlaying) {
                if (!startedReported) {
                    startedReported = true
                    report("started")
                } else {
                    report("position")
                }
            } else if (!player.playWhenReady && player.playbackState != Player.STATE_ENDED) {
                report("paused")   // a real pause, not buffering or a vocal-level swap
            }
        }

        override fun onPlaybackStateChanged(state: Int) {
            if (state == Player.STATE_ENDED && current != null) {
                report("ended")
                finishCurrent()
            }
        }

        override fun onPlayerError(error: PlaybackException) {
            Log.e(TAG, "player error", error)
            if (current == null) return
            if (!connected) {   // the server is down and the buffer ran out: wait for it instead of skipping
                waitingForServer = true
                showToast("Server offline, the song continues when it's back", sticky = true)
                return
            }
            if (++errors <= 3) {
                showToast("Playback problem, retrying…")
                handler.postDelayed({ if (current != null) player.prepare() }, 2000)
            } else {
                report("skip")
                stopCurrent("Couldn't play this song, skipped")
            }
        }
    }

    private fun report(event: String) {
        val e = current ?: return
        if (event == "ended" || event == "skip") {   // must reach the server, also if it is down right now
            EndReports.send(this, client.base, e.id, event, player.currentPosition / 1000.0)
            return
        }
        val base = client.base ?: return
        val body = JSONObject().put("entry_id", e.id).put("position", player.currentPosition / 1000.0)
        Http.post("$base/api/player/$event", body) { code, _ ->
            // the server no longer has this entry playing (e.g. skipped while we were offline)
            if (code == 409 && event != "ended" && event != "skip" && current?.id == e.id) stopCurrent("Song ended on the server")
        }
    }

    private fun finishCurrent() {
        current = null
        lyrics = null
        startedReported = false
        confirmSkip = false
        waitingForServer = false
        player.stop()
        player.clearMediaItems()
        lyricsView.lyrics = null
        showLobby()
        pendingStats?.let {
            pendingStats = null
            showStats(it)
        }
    }

    private fun stopCurrent(msg: String) {
        finishCurrent()
        showToast(msg)
    }

    private fun togglePlay() {
        if (player.playWhenReady) player.pause() else player.play()
        showToast(if (player.playWhenReady) "▶" else "❚❚  Paused")
    }

    private fun changeLevel(dir: Int) {
        val e = current ?: return
        val base = client.base ?: return
        val i = VOCAL_LEVELS.indexOf(level) + dir
        if (i !in VOCAL_LEVELS.indices) {
            showToast("Backing vocals $level %")
            return
        }
        val next = VOCAL_LEVELS[i]
        if (next !in levelsReady) {
            showToast("Backing vocals $next % isn't ready yet")
            return
        }
        // same song, key and position; only the pre-rendered stream changes
        player.setMediaItem(MediaItem.fromUri(base + e.mediaUrlFor(next)), player.currentPosition)
        player.prepare()
        level = next
        showToast("Backing vocals $next %")
    }

    private fun changeOffset(deltaMs: Int) {
        val l = lyrics ?: return
        l.offsetMs = (l.offsetMs + deltaMs).coerceIn(-10_000, 10_000)
        val sec = "%+.1f s".format(l.offsetMs / 1000.0)
        showToast("Lyrics $sec" + when {
            l.offsetMs > 0 -> " (later)"
            l.offsetMs < 0 -> " (earlier)"
            else -> ""
        })
        handler.removeCallbacks(sendOffset)
        handler.postDelayed(sendOffset, 600)   // one request after the last press, not one per press
        offsetPending = true
    }

    private fun postOffset() {
        offsetPending = false
        val e = current ?: return
        val l = lyrics ?: return
        val base = client.base ?: return
        Http.post("$base/api/offset/${e.songId}", JSONObject().put("offset_ms", l.offsetMs))
    }

    // --- stats -----------------------------------------------------------------------------------------------

    private fun showStats(msg: JSONObject) {
        stats.show(msg)
        stats.fadeIn()
        handler.removeCallbacks(hideStats)
        handler.postDelayed(hideStats, Timing.STATS_MS)
    }

    private fun closeStats() {
        handler.removeCallbacks(hideStats)
        stats.visibility = View.GONE
    }

    // --- Wake-on-LAN -----------------------------------------------------------------------------------------

    private fun wakePc() {
        val prefs = Http.prefs(this)
        val mac = prefs.getString("mac", null)?.let(Wol::parseMac)
        if (mac == null) {
            askText("PC MAC address (aa:bb:cc:dd:ee:ff)", "") { text ->
                val m = Wol.parseMac(text)
                if (m == null) showToast("That isn't a MAC address") else {
                    prefs.edit().putString("mac", Wol.format(m)).apply()
                    wakePc()
                }
            }
            return
        }
        Wol.send(mac, Wol.subnetBroadcast(prefs.getString("server_ip", null)))
        showToast("Wake-on-LAN sent to ${Wol.format(mac)}.\nGive the PC a minute to start.")
    }

    // --- keys ------------------------------------------------------------------------------------------------

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (offlineLayer.visibility == View.VISIBLE && keyCode != KeyEvent.KEYCODE_BACK) return super.onKeyDown(keyCode, event)
        if (stats.visibility == View.VISIBLE) {          // OK or Back closes it; other keys do nothing
            if (isOk(keyCode) || keyCode == KeyEvent.KEYCODE_BACK) {
                if (event.repeatCount == 0) closeStats()
            }
            return true
        }
        when (keyCode) {
            KeyEvent.KEYCODE_DPAD_CENTER, KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_NUMPAD_ENTER -> {
                if (event.repeatCount == 0) event.startTracking()
                return true
            }
            KeyEvent.KEYCODE_MENU -> {
                if (event.repeatCount == 0) requestSkip()
                return true
            }
            KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> {
                if (showingPlayer) togglePlay()
                return true
            }
            KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_DPAD_RIGHT -> if (showingPlayer) {
                changeOffset(if (keyCode == KeyEvent.KEYCODE_DPAD_RIGHT) OFFSET_STEP else -OFFSET_STEP)
                return true
            }
            KeyEvent.KEYCODE_DPAD_UP, KeyEvent.KEYCODE_DPAD_DOWN -> if (showingPlayer) {
                if (event.repeatCount == 0) changeLevel(if (keyCode == KeyEvent.KEYCODE_DPAD_UP) 1 else -1)
                return true
            }
            KeyEvent.KEYCODE_BACK -> {
                if (event.repeatCount == 0) back()
                return true
            }
        }
        return super.onKeyDown(keyCode, event)
    }

    private fun isOk(keyCode: Int) =
        keyCode == KeyEvent.KEYCODE_DPAD_CENTER || keyCode == KeyEvent.KEYCODE_ENTER || keyCode == KeyEvent.KEYCODE_NUMPAD_ENTER

    override fun onKeyLongPress(keyCode: Int, event: KeyEvent): Boolean {
        if (isOk(keyCode)) {
            requestSkip()
            return true
        }
        return super.onKeyLongPress(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (offlineLayer.visibility == View.VISIBLE) return super.onKeyUp(keyCode, event)
        if (isOk(keyCode)) {
            if (event.isTracking && !event.isCanceled) ok()   // a long press cancels the short press
            return true
        }
        return super.onKeyUp(keyCode, event)
    }

    private fun ok() {
        when {
            confirmSkip -> doSkip()
            showingPlayer -> togglePlay()
            current != null -> {                       // lobby, song paused: resume
                showPlayer()
                player.play()
            }
            pendingYt != null -> pendingYt?.let { e -> pendingYt = null; playYoutube(e) }
            YtWatchService.active != null -> Unit
            else -> {
                countdownCancelled = false
                startNext()
            }
        }
    }

    private fun back() {
        when {
            confirmSkip -> {
                confirmSkip = false
                toast.visibility = View.GONE
            }
            showingPlayer -> {                          // never a skip: pause and show the lobby
                player.pause()
                showLobby()
            }
            countdownEnd > 0 -> {
                countdownCancelled = true
                countdownEnd = 0
                updateLobby()
            }
        }
    }

    private fun requestSkip() {
        if (current == null && pendingYt == null) return
        confirmSkip = true
        showToast("Skip this song?   OK = yes   ·   Back = no", sticky = true)
    }

    private fun doSkip() {
        confirmSkip = false
        toast.visibility = View.GONE
        if (current != null) {
            report("skip")
            stopCurrent("Skipped")
        } else pendingYt?.let { e ->
            pendingYt = null
            client.base?.let { Http.post("$it/api/player/skip", JSONObject().put("entry_id", e.id).put("position", 0)) }
            showToast("Skipped")
        }
    }

    // --- screens ---------------------------------------------------------------------------------------------

    private fun showPlayer() {
        showingPlayer = true
        countdownEnd = 0
        offlineLayer.visibility = View.GONE
        closeStats()
        lobby.visibility = View.GONE
        playerLayer.fadeIn()
        lyricsView.start()
    }

    private fun showLobby() {
        showingPlayer = false
        lyricsView.stop()
        playerLayer.visibility = View.GONE
        lobby.fadeIn()
        if (!connected) showOffline()
        updateLobby()
    }

    private fun showOffline() {
        offlineLayer.visibility = View.VISIBLE
        val hasMac = Http.prefs(this).getString("mac", null) != null
        wolButton.text = if (hasMac) "Wake PC" else "Wake PC (enter MAC)"
        (if (hasMac) wolButton else offlineLayer.findViewWithTag<View>("ip"))?.requestFocus()
    }

    /** Lobby banner + the auto-start countdown (10 s whenever nothing plays and an entry is ready). */
    private fun updateLobby() {
        val cur = current
        val yt = YtWatchService.active
        val pyt = pendingYt
        val next = queue?.nextReady
        val text = when {
            starting -> "Starting…"
            yt != null -> "Playing on YouTube: ${yt.title}"
            cur != null -> "Paused: ${cur.title} (${cur.singerText})\nOK = resume  ·  hold OK = skip"
            pyt != null -> "Was playing on YouTube: ${pyt.title}\nOK = play again  ·  hold OK = skip"
            !connected -> "Looking for the karaoke server…"
            next == null -> if (queue?.upcoming.isNullOrEmpty()) "Scan the code and add a song!" else "Preparing songs…"
            countdownCancelled -> "Up next: ${next.singerText}, ${next.title}\nPress OK to start"
            stats.visibility == View.VISIBLE -> "Up next: ${next.singerText}, ${next.title}\nClose the stats to start"
            else -> {
                val now = SystemClock.elapsedRealtime()
                if (countdownEnd == 0L) countdownEnd = now + COUNTDOWN_MS
                val left = ceil((countdownEnd - now) / 1000.0).toInt()
                if (left <= 0) {
                    startNext()
                    "Starting…"
                } else {
                    "Up next: ${next.singerText}, ${next.title}\nStarting in $left s  ·  OK = now  ·  Back = wait"
                }
            }
        }
        val counting = !starting && yt == null && cur == null && pyt == null && connected && next != null &&
            !countdownCancelled && stats.visibility != View.VISIBLE
        if (!counting) countdownEnd = 0
        lobby.setBanner(text)
    }

    /** The player's footer: "Next · Lena – Song A · Jonas – Song B", or empty. */
    private fun nextLineText(entries: List<Entry>): String {
        if (entries.isEmpty()) return ""
        val max = Space.NEXT_UP_TITLE_CHARS
        return (listOf("Next") + entries.map { e ->
            val title = if (e.title.length > max) e.title.take(max - 1).trimEnd() + "…" else e.title
            "${e.singerText} – $title"
        }).joinToString("   ·   ")
    }

    private fun updatePlayerOverlay(e: Entry) {
        singersText.text = if (e.singers.size == 2) {   // duet: each name in its singer's color
            SpannableStringBuilder().apply {
                e.singers.forEachIndexed { i, name ->
                    if (i > 0) append("  &  ")
                    val start = length
                    append(name)
                    setSpan(ForegroundColorSpan(Colors.DUET[i]), start, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                }
            }
        } else e.singerText
        val parts = mutableListOf(e.titleText)
        if (e.keyShift != 0) parts += "Key %+d".format(e.keyShift)
        if (lyrics?.source == "auto") parts += "auto lyrics"
        titleText.text = parts.joinToString("   ·   ")
    }

    private fun showToast(text: String, sticky: Boolean = false) {
        toast.text = text
        toast.visibility = View.VISIBLE
        handler.removeCallbacks(hideToast)
        if (!sticky) handler.postDelayed(hideToast, Timing.TOAST_MS)
    }

    private fun askText(title: String, initial: String, onOk: (String) -> Unit) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(initial)
            setSelection(text.length)
            setTextSize(TypedValue.COMPLEX_UNIT_PX, px(Text.DIALOG_INPUT))
        }
        AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setPositiveButton("OK") { _, _ -> onOk(input.text.toString()) }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun askServerIp() =
        askText("Server address (IP or IP:port)", client.base?.removePrefix("http://") ?: "192.168.") { client.setManual(it) }

    private fun button(text: String, tag: String, onClick: () -> Unit) = Button(this).apply {
        this.tag = tag
        this.text = text
        isAllCaps = false
        typeface = Theme.bold
        setTextSize(TypedValue.COMPLEX_UNIT_PX, px(Text.BUTTON))
        setPadding(pxi(Space.BUTTON_PAD_H), pxi(Space.BUTTON_PAD_V), pxi(Space.BUTTON_PAD_H), pxi(Space.BUTTON_PAD_V))
        fun bg(color: Int) = roundRect(color, Space.PILL_RADIUS)
        background = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), bg(Colors.BUTTON_FOCUSED_BG))
            addState(intArrayOf(), bg(Colors.BUTTON_BG))
        }
        setTextColor(ColorStateList(arrayOf(intArrayOf(android.R.attr.state_focused), intArrayOf()),
            intArrayOf(Colors.BUTTON_FOCUSED_TEXT, Colors.BUTTON_TEXT)))
        setOnClickListener { onClick() }
    }

    private fun buildViews(): View {
        val root = FrameLayout(this).apply { setBackgroundColor(Colors.BG) }
        val match = FrameLayout.LayoutParams.MATCH_PARENT
        val wrap = FrameLayout.LayoutParams.WRAP_CONTENT

        lobby = LobbyView(this)
        root.addView(lobby, FrameLayout.LayoutParams(match, match))

        playerLayer = FrameLayout(this).apply {
            visibility = View.GONE
            background = GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM, Colors.PLAYER_GRADIENT)
        }
        lyricsView = LyricsView(this)
        playerLayer.addView(lyricsView, FrameLayout.LayoutParams(match, match))
        val top = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            singersText = label(Text.SINGERS, Colors.SUNG, bold = true).apply {   // the singer chip
                background = roundRect(Colors.CHIP_BG, Space.PILL_RADIUS, Colors.CHIP_BORDER)
                setPadding(pxi(Space.CHIP_PAD_H), pxi(Space.CHIP_PAD_V), pxi(Space.CHIP_PAD_H), pxi(Space.CHIP_PAD_V))
            }
            titleText = label(Text.SONG_INFO, Colors.MUTED)
            addView(singersText, LinearLayout.LayoutParams(wrap, wrap))
            addView(titleText, LinearLayout.LayoutParams(wrap, wrap).apply { topMargin = pxi(Space.GAP_M) })
        }
        playerLayer.addView(top, FrameLayout.LayoutParams(match, wrap, Gravity.TOP).apply { topMargin = pxi(Space.PLAYER_TOP) })
        nextLine = label(Text.NEXT_UP, Colors.NEXT_UP).apply { gravity = Gravity.CENTER }
        playerLayer.addView(nextLine, FrameLayout.LayoutParams(match, wrap, Gravity.BOTTOM).apply {
            bottomMargin = pxi(Space.PLAYER_BOTTOM); leftMargin = pxi(Space.PLAYER_SIDE); rightMargin = pxi(Space.PLAYER_SIDE)
        })
        offlineNote = label(Text.OFFLINE_NOTE, Colors.BAD, medium = true).apply {
            text = "Server offline"
            visibility = View.GONE
            background = roundRect(Colors.PANEL, Space.PILL_RADIUS, Colors.BAD, Space.BADGE_BORDER)
            setPadding(pxi(Space.CHIP_PAD_H), pxi(Space.CHIP_PAD_V), pxi(Space.CHIP_PAD_H), pxi(Space.CHIP_PAD_V))
        }
        playerLayer.addView(offlineNote, FrameLayout.LayoutParams(wrap, wrap, Gravity.TOP or Gravity.END).apply {
            topMargin = pxi(Space.CORNER_NOTE); rightMargin = pxi(Space.CORNER_NOTE)
        })
        root.addView(playerLayer, FrameLayout.LayoutParams(match, match))

        stats = StatsView(this).apply { visibility = View.GONE }
        root.addView(stats, FrameLayout.LayoutParams(match, match))

        reactions = ReactionsView(this)
        root.addView(reactions, FrameLayout.LayoutParams(match, match))

        offlineLayer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Colors.SCRIM)
            offlineTitle = label(Text.OFFLINE_TITLE, Colors.FAILED, bold = true).apply { text = "Connecting…" }
            offlineDetail = label(Text.OFFLINE_DETAIL, Colors.DIM, lines = 2).apply {
                text = "Looking for the karaoke server on the network…"
                gravity = Gravity.CENTER
            }
            addView(offlineTitle, LinearLayout.LayoutParams(wrap, wrap))
            addView(offlineDetail, LinearLayout.LayoutParams(wrap, wrap).apply { topMargin = pxi(Space.GAP_L) })
            wolButton = button("Wake PC", "wol") { wakePc() }
            val buttons = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                addView(wolButton)
                addView(button("Enter server IP", "ip") { askServerIp() },
                    LinearLayout.LayoutParams(wrap, wrap).apply { leftMargin = pxi(Space.GAP_L) })
            }
            addView(buttons, LinearLayout.LayoutParams(wrap, wrap).apply { topMargin = pxi(Space.GAP_XL) })
        }
        root.addView(offlineLayer, FrameLayout.LayoutParams(match, match))

        toast = label(Text.TOAST, Colors.TEXT, bold = true, lines = 2).apply {
            visibility = View.GONE
            gravity = Gravity.CENTER
            val p = pxi(Space.TOAST_PAD)
            setPadding(p * 2, p, p * 2, p)
            background = GradientDrawable().apply {
                setColor(Colors.TOAST_BG)
                cornerRadius = px(Space.TOAST_RADIUS)
            }
        }
        root.addView(toast, FrameLayout.LayoutParams(wrap, wrap, Gravity.CENTER_HORIZONTAL or Gravity.BOTTOM).apply {
            bottomMargin = pxi(Space.TOAST_BOTTOM)
        })
        return root
    }

    companion object {
        val VOCAL_LEVELS = listOf(0, 5, 10, 15)
        const val OFFSET_STEP = 100
        const val COUNTDOWN_MS = 10_000L
    }
}
