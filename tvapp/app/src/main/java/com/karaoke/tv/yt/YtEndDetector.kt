package com.karaoke.tv.yt

import kotlin.math.abs

/**
 * Decides from YouTube's media session when the requested video has ended (SPEC section 9). Pure logic, fed with
 * snapshots by [YtWatchService], so it can be unit-tested.
 *
 * - Nothing counts until the requested video has been seen. YouTube app versions publish different metadata, so
 *   the video is recognized by the best signal the session offers ([match]):
 *   1. the media id contains the video id;
 *   2. the title matches (and, if the session has a duration, it is within ±15 s of the expected one);
 *   3. no title, but a duration within ±15 s of the expected one;
 *   4. no metadata at all: the session has been playing for about as long as the video.
 *   Pre-roll ads don't match (seen on YouTube TV: empty title + the ad's duration, or the video's title + the ad's
 *   duration), so they don't count.
 * - After that, the video has ended when the session is STOPPED/NONE or gone, when it is PAUSED at
 *   position ≥ duration − 2 s (end screen with autoplay off), or when it switches to another item (autoplay, post-roll
 *   ad) after our video got within 10 s of its end at any point. An earlier switch is taken as a mid-roll ad and ignored.
 * - An end condition must hold for [SETTLE_MS] (one-off glitches between ad and video don't end it).
 * - If the session never shows anything usable, [YtWatchService]'s fallback timer (duration + 60 s) ends it.
 */
class YtEndDetector(private val videoId: String, title: String, private val expectedMs: Long) {
    /** One look at the YouTube session; null = YouTube has no active session. */
    data class Snapshot(val state: Int, val positionMs: Long, val mediaId: String?, val title: String?, val durationMs: Long) {
        /** No metadata at all (tier 4). */
        val blind get() = mediaId.isNullOrBlank() && title.isNullOrBlank() && durationMs <= 0
    }

    var seen = false
        private set
    var lastPositionMs = 0L
        private set

    /** How the video was recognized ("id", "title", "duration", "play time"), for logging; null until seen. */
    var seenBy: String? = null
        private set

    /** Furthest position reached in our video. YouTube can reset the position to 0 while the metadata still shows
     *  our video (just before a post-roll ad), so "near the end" must not depend on the latest position. */
    var furthestMs = 0L
        private set
    private var durationMs = expectedMs
    private val titleWords = words(title)
    private var endSince = -1L

    /** How long a session without any metadata has been playing (tier 4). */
    private var blindPlayMs = 0L
    private var blindPlayingSince = -1L

    /** The end condition that currently holds (still settling, or reached), for logging. */
    var endReason: String? = null
        private set

    /** Which tier (1-3, see the class comment) recognizes [s] as the requested video, or null if none does. */
    fun match(s: Snapshot): String? {
        if (s.mediaId?.contains(videoId) == true) return "id"
        val durationFits = expectedMs > 0 && s.durationMs > 0 && abs(s.durationMs - expectedMs) <= DURATION_TOLERANCE_MS
        if (!s.title.isNullOrBlank()) {
            if (!titleMatches(words(s.title))) return null
            return if (expectedMs <= 0 || s.durationMs <= 0 || durationFits) "title" else null
        }
        return if (durationFits) "duration" else null
    }

    fun matches(s: Snapshot) = match(s) != null

    /** Returns true once the video has ended. [nowMs] = a monotonic clock. */
    fun update(s: Snapshot?, nowMs: Long): Boolean {
        trackBlindPlay(s, nowMs)
        val tier = s?.let { match(it) }
        if (tier != null && s!!.state in ACTIVE) {
            if (!seen && s.durationMs > 0) durationMs = s.durationMs
            if (!seen) seenBy = tier
            seen = true
        } else if (!seen && expectedMs > 0 && blindPlayMs >= expectedMs - DURATION_TOLERANCE_MS) {
            seen = true
            seenBy = "play time"
        }
        // tier 4: once recognized by play time, the metadata-less snapshots are ours
        val ours = tier != null || (s != null && s.blind && seenBy == "play time")
        if (ours) {
            lastPositionMs = s!!.positionMs
            furthestMs = maxOf(furthestMs, s.positionMs)
        }
        endReason = if (!seen) null else when {
            s == null -> "session gone"
            s.state == STATE_STOPPED || s.state == STATE_NONE -> "state ${s.state}"
            ours && s.state == STATE_PAUSED && durationMs > 0 && s.positionMs >= durationMs - END_MARGIN_MS -> "paused at the end"
            !ours && !s.blind && s.title != null && durationMs > 0 && furthestMs >= durationMs - AUTOPLAY_MARGIN_MS -> "next item after the end"
            else -> null
        }
        if (endReason == null) {
            endSince = -1
            return false
        }
        if (endSince < 0) endSince = nowMs
        return nowMs - endSince >= SETTLE_MS
    }

    private fun trackBlindPlay(s: Snapshot?, nowMs: Long) {
        if (blindPlayingSince >= 0) blindPlayMs += nowMs - blindPlayingSince
        blindPlayingSince = if (s != null && s.blind && s.state == STATE_PLAYING) nowMs else -1L
    }

    private fun titleMatches(w: List<String>): Boolean {
        if (w.isEmpty() || titleWords.isEmpty()) return false
        if (w == titleWords) return true
        val a = w.toSet()
        val b = titleWords.toSet()
        return a.intersect(b).size >= 0.6 * minOf(a.size, b.size)
    }

    companion object {
        // android.media.session.PlaybackState values (kept here so the logic runs in plain JVM tests)
        const val STATE_NONE = 0
        const val STATE_STOPPED = 1
        const val STATE_PAUSED = 2
        const val STATE_PLAYING = 3
        const val STATE_BUFFERING = 6
        private val ACTIVE = setOf(STATE_PLAYING, STATE_PAUSED, STATE_BUFFERING)

        const val DURATION_TOLERANCE_MS = 15_000L
        const val END_MARGIN_MS = 2_000L
        const val AUTOPLAY_MARGIN_MS = 10_000L
        const val SETTLE_MS = 1_500L

        fun words(s: String) = s.lowercase().split(Regex("[^\\p{L}\\p{N}]+")).filter { it.isNotEmpty() }
    }
}
