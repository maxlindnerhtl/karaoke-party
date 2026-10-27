package com.karaoke.tv.yt

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import com.karaoke.tv.EndReports
import com.karaoke.tv.Entry
import com.karaoke.tv.Http
import com.karaoke.tv.MainActivity
import com.karaoke.tv.TAG
import java.util.Collections

/**
 * Foreground service for one YouTube entry (SPEC section 9). Watches the media session of the YouTube app and,
 * once [YtEndDetector] says the video has ended (or the fallback timer of duration + 60 s runs out), reports the end
 * to the server itself (the Activity may have been killed while YouTube was in front) and brings the karaoke app
 * back. If the user comes back early (Back in YouTube), MainActivity calls [returned] and the queue continues.
 */
class YtWatchService : Service() {
    data class Watch(val entryId: Int, val videoId: String, val title: String, val durationMs: Long, val pkg: String?)

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var msm: MediaSessionManager
    private lateinit var listenerComponent: ComponentName
    private var watch: Watch? = null
    private var detector: YtEndDetector? = null
    private var packages: Set<String> = emptySet()
    private var sessionsAvailable = true
    private var controller: MediaController? = null
    private var lastLogged = ""
    private var lastReason: String? = null
    private var lastPollLog = 0L
    private var done = false

    private val poll = object : Runnable {
        override fun run() {
            check()
            if (!done) handler.postDelayed(this, POLL_MS)
        }
    }
    private val fallback = Runnable { finish("fallback timer", byUser = false) }
    private val sessionsChanged = MediaSessionManager.OnActiveSessionsChangedListener { check() }
    private val controllerCallback = object : MediaController.Callback() {
        override fun onPlaybackStateChanged(state: PlaybackState?) = check()
        override fun onMetadataChanged(metadata: MediaMetadata?) = check()
        override fun onSessionDestroyed() = check()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        msm = getSystemService(Context.MEDIA_SESSION_SERVICE) as MediaSessionManager
        listenerComponent = ComponentName(this, YtNotificationListener::class.java)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> begin(intent)
            ACTION_RETURNED -> finish("user returned", byUser = true)
        }
        return START_NOT_STICKY
    }

    private fun begin(intent: Intent) {
        val w = Watch(intent.getIntExtra("entry_id", -1), intent.getStringExtra("video_id")!!,
            intent.getStringExtra("title") ?: "", intent.getLongExtra("duration_ms", 0), intent.getStringExtra("package"))
        watch = w
        packages = YouTubeApps.sessionPackages(w.pkg) + extraSessionPackages
        active = w
        done = false
        detector = YtEndDetector(w.videoId, w.title, w.durationMs)
        startForeground(NOTIFICATION_ID, notification(w.title))
        try {
            msm.addOnActiveSessionsChangedListener(sessionsChanged, listenerComponent, handler)
            sessionsAvailable = true
        } catch (e: SecurityException) {
            sessionsAvailable = false
            Log.w(TAG, "yt: no notification-listener grant, only the fallback timer works: $e")
        }
        handler.post(poll)
        val timeout = (if (w.durationMs > 0) w.durationMs else DEFAULT_DURATION_MS) + FALLBACK_EXTRA_MS
        handler.postDelayed(fallback, timeout)
        Log.i(TAG, "yt: watching entry ${w.entryId} video ${w.videoId} in ${w.pkg} (${w.durationMs / 1000} s, " +
            "fallback in ${timeout / 1000} s, API ${Build.VERSION.SDK_INT}, can return by itself: ${canReturnByItself(this)})")
    }

    private fun check() {
        val d = detector ?: return
        if (done || !sessionsAvailable) return
        val snap = try {
            snapshot()
        } catch (e: SecurityException) {
            sessionsAvailable = false
            Log.w(TAG, "yt: lost media-session access: $e")
            return
        }
        val desc = snap?.let { "state=${it.state} pos=${it.positionMs / 1000}s id=${it.mediaId} title=${it.title} dur=${it.durationMs / 1000}s" } ?: "no session"
        // a position jump back to the start is logged too (it hid the cause of a missed end once)
        val key = snap?.let { "${it.state}|${it.mediaId}|${it.title}|${it.durationMs}|${it.positionMs < 5_000}" } ?: "none"
        if (key != lastLogged) {
            lastLogged = key
            Log.i(TAG, "yt: $desc (seen=${d.seen} by ${d.seenBy})")
        }
        val now = SystemClock.elapsedRealtime()
        val ended = d.update(snap, now)
        if (d.endReason != lastReason || now - lastPollLog >= 10_000) {
            lastReason = d.endReason
            lastPollLog = now
            Log.i(TAG, "yt: poll $desc lastPos=${d.lastPositionMs / 1000}s furthest=${d.furthestMs / 1000}s end=${d.endReason}")
        }
        if (ended) finish("session ended: ${d.endReason}; $desc", byUser = false)
    }

    private fun snapshot(): YtEndDetector.Snapshot? {
        val c = msm.getActiveSessions(listenerComponent).firstOrNull { it.packageName in packages }
        if (c?.sessionToken != controller?.sessionToken) {
            controller?.unregisterCallback(controllerCallback)
            controller = c
            c?.registerCallback(controllerCallback, handler)
        }
        if (c == null) return null
        val ps = c.playbackState
        val meta = c.metadata
        var pos = ps?.position ?: 0L
        if (ps != null && ps.state == PlaybackState.STATE_PLAYING && ps.lastPositionUpdateTime > 0) {
            pos += ((SystemClock.elapsedRealtime() - ps.lastPositionUpdateTime) * ps.playbackSpeed).toLong()
        }
        return YtEndDetector.Snapshot(
            state = ps?.state ?: PlaybackState.STATE_NONE,
            positionMs = pos,
            mediaId = meta?.getString(MediaMetadata.METADATA_KEY_MEDIA_ID),
            title = meta?.getString(MediaMetadata.METADATA_KEY_TITLE) ?: meta?.description?.title?.toString(),
            durationMs = meta?.getLong(MediaMetadata.METADATA_KEY_DURATION) ?: 0L,
        )
    }

    private fun finish(reason: String, byUser: Boolean) {
        val w = watch ?: return stopSelf()
        if (done) return
        done = true
        active = null
        finished.add(w.entryId)
        handler.removeCallbacks(poll)
        handler.removeCallbacks(fallback)
        val d = detector!!
        // came back before the video was ever seen (e.g. during the ad): skipped, unless we can't see sessions at all
        val event = if (d.seen || !sessionsAvailable || !byUser) "ended" else "skip"
        Log.i(TAG, "yt: entry ${w.entryId} done ($reason) -> $event")
        EndReports.send(this, Http.base(this), w.entryId, event, d.lastPositionMs / 1000.0)
        if (!byUser) {
            // Android 10+ only lets a background app start an activity with an exemption: a bound notification
            // listener or "display over other apps". Without one the call is dropped; the queue still continues,
            // and the lobby is there when someone presses Back / Home.
            if (!canReturnByItself(this)) Log.w(TAG, "yt: no background-start exemption; press Back to return")
            startActivity(Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT))
        }
        @Suppress("DEPRECATION") stopForeground(true)   // STOP_FOREGROUND_REMOVE needs API 33
        stopSelf()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        controller?.unregisterCallback(controllerCallback)
        runCatching { msm.removeOnActiveSessionsChangedListener(sessionsChanged) }
        if (active == watch) active = null
        super.onDestroy()
    }

    private fun notification(title: String): Notification {
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "YouTube playback", NotificationManager.IMPORTANCE_LOW))
            Notification.Builder(this, CHANNEL)
        } else {
            @Suppress("DEPRECATION") Notification.Builder(this)
        }
        return b.setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Karaoke: playing on YouTube")
            .setContentText(title)
            .build()
    }

    companion object {
        private const val ACTION_START = "com.karaoke.tv.YT_START"
        private const val ACTION_RETURNED = "com.karaoke.tv.YT_RETURNED"
        private const val CHANNEL = "youtube"
        private const val NOTIFICATION_ID = 1
        private const val POLL_MS = 1_000L
        private const val FALLBACK_EXTRA_MS = 60_000L
        private const val DEFAULT_DURATION_MS = 8 * 60_000L

        /** The YouTube entry being watched, or null. */
        @Volatile
        var active: Watch? = null
            private set

        /** Entries this app has already finished; the lobby ignores them while the server catches up. */
        val finished: MutableSet<Int> = Collections.synchronizedSet(HashSet())

        /** Session packages watched in addition to the YouTube apps (instrumented tests publish a fake session). */
        @Volatile
        var extraSessionPackages: Set<String> = emptySet()

        /** Called while the app is in front (a foreground app may start the service). [pkg] = the app that plays it. */
        fun start(ctx: Context, e: Entry, pkg: String?) {
            ctx.startService(Intent(ctx, YtWatchService::class.java).setAction(ACTION_START)
                .putExtra("entry_id", e.id).putExtra("video_id", e.videoId).putExtra("title", e.title)
                .putExtra("duration_ms", (e.duration * 1000).toLong()).putExtra("package", pkg))
        }

        /** The karaoke app is in front again while the watch runs (Back in YouTube): continue the queue. */
        fun returned(ctx: Context) {
            ctx.startService(Intent(ctx, YtWatchService::class.java).setAction(ACTION_RETURNED))
        }

        fun listenerComponent(ctx: Context) = ComponentName(ctx, YtNotificationListener::class.java)

        /** Whether the adb grant for [YtNotificationListener] is in place. */
        fun listenerEnabled(ctx: Context): Boolean {
            if (Build.VERSION.SDK_INT >= 27) {
                val nm = ctx.getSystemService(NotificationManager::class.java)
                return nm.isNotificationListenerAccessGranted(listenerComponent(ctx))
            }
            val list = Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners") ?: ""
            return list.split(':').any { ComponentName.unflattenFromString(it) == listenerComponent(ctx) }
        }

        /** Whether the service may bring the app back to the front from the background (see [finish]). */
        fun canReturnByItself(ctx: Context) =
            Build.VERSION.SDK_INT < 29 || listenerEnabled(ctx) || Settings.canDrawOverlays(ctx)

        /** The adb commands that grant the notification listener on this Android version (shown in the lobby). */
        fun grantCommands(ctx: Context): List<String> {
            val c = listenerComponent(ctx).flattenToString()
            val listener = if (Build.VERSION.SDK_INT >= 26) "adb shell cmd notification allow_listener $c"
                // Android 7 has no allow_listener: append to the list (keep existing entries, separated by ':')
                else "adb shell settings put secure enabled_notification_listeners <current entries>:$c"
            return listOf(listener)
        }
    }
}
