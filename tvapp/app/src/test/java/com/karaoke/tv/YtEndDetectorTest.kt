package com.karaoke.tv

import com.karaoke.tv.yt.YtEndDetector
import com.karaoke.tv.yt.YtEndDetector.Companion.STATE_PAUSED
import com.karaoke.tv.yt.YtEndDetector.Companion.STATE_PLAYING
import com.karaoke.tv.yt.YtEndDetector.Companion.STATE_STOPPED
import com.karaoke.tv.yt.YtEndDetector.Snapshot
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class YtEndDetectorTest {
    private val title = "Example Artist - Example Song (Karaoke Version)"
    private fun det() = YtEndDetector("abc123", title, 355_000)
    private fun video(state: Int, pos: Long) = Snapshot(state, pos, null, title, 355_000)
    private val ad = Snapshot(STATE_PLAYING, 5_000, null, "Buy Shoes Now", 15_000)

    /** Feeds the same snapshot twice, [YtEndDetector.SETTLE_MS] apart. */
    private fun YtEndDetector.settled(s: Snapshot?, t: Long) = update(s, t) || update(s, t + 1_500)

    @Test
    fun preRollAdAndStopBeforeTheVideoDoNotEnd() {
        val d = det()
        assertFalse(d.settled(ad, 0))
        assertFalse(d.settled(Snapshot(STATE_STOPPED, 0, null, "Buy Shoes Now", 15_000), 10_000))
        assertFalse(d.settled(null, 20_000))
        assertFalse(d.seen)
    }

    @Test
    fun adWithTheVideoTitleButWrongDurationIsNotTheVideo() {
        val d = det()
        d.update(Snapshot(STATE_PLAYING, 1_000, null, title, 20_000), 0)
        assertFalse(d.seen)
    }

    @Test
    fun stoppedAfterTheVideoEnds() {
        val d = det()
        assertFalse(d.settled(video(STATE_PLAYING, 1_000), 0))
        assertTrue(d.seen)
        assertFalse(d.update(video(STATE_STOPPED, 354_000), 400_000))   // must hold for a moment
        assertTrue(d.update(video(STATE_STOPPED, 354_000), 401_600))
    }

    @Test
    fun sessionGoneAfterSeenEnds() {
        val d = det()
        d.update(video(STATE_PLAYING, 1_000), 0)
        assertTrue(d.settled(null, 100_000))
    }

    @Test
    fun pausedOnTheEndScreenEndsButAPauseMidSongDoesNot() {
        val d = det()
        d.update(video(STATE_PLAYING, 1_000), 0)
        assertFalse(d.settled(video(STATE_PAUSED, 120_000), 120_000))
        assertTrue(d.settled(video(STATE_PAUSED, 353_500), 360_000))
    }

    @Test
    fun mediaIdMatchIgnoresTitle() {
        val d = det()
        d.update(Snapshot(STATE_PLAYING, 1_000, "yt:abc123", "something else", 0), 0)
        assertTrue(d.seen)
    }

    @Test
    fun autoplayToAnotherVideoNearTheEndEndsButAMidRollAdDoesNot() {
        val d = det()
        d.update(video(STATE_PLAYING, 1_000), 0)
        d.update(video(STATE_PLAYING, 100_000), 100_000)
        assertFalse(d.settled(ad, 101_000))                 // mid-roll ad
        d.update(video(STATE_PLAYING, 350_000), 350_000)
        assertTrue(d.settled(Snapshot(STATE_PLAYING, 0, null, "Another Song Karaoke", 200_000), 356_000))
    }

    @Test
    fun titleMatchToleratesSmallDifferences() {
        val d = det()
        d.update(Snapshot(STATE_PLAYING, 1_000, null, "Example Song (Karaoke Version) - Example Artist", 356_000), 0)
        assertTrue(d.seen)
    }

    @Test
    fun postRollAdWithTheVideoTitleAfterTheEnd() {
        // seen on the TV (YouTube 5.30): after the video, a 15 s ad keeps the video's title, then the session goes away
        val d = YtEndDetector("vid00000002", "Example Artist - Example Song (Karaoke Version)", 375_000)
        val t = "Example Artist - Example Song (Karaoke Version)"
        d.update(Snapshot(STATE_PLAYING, 1_000, null, t, 374_000), 0)
        d.update(Snapshot(STATE_PLAYING, 371_000, null, t, 374_000), 370_000)
        d.update(Snapshot(STATE_PLAYING, 374_000, null, t, 374_000), 373_000)
        assertTrue(d.settled(Snapshot(STATE_PLAYING, 0, null, t, 15_000), 378_000))
    }

    @Test
    fun positionResetToZeroBeforeAPostRollAdStillEnds() {
        // replayed from a real logcat (YouTube TV 5.30, a 199 s video): at the end the position jumped to 0 while
        // the metadata still showed the video, then a 20 s post-roll ad started with the same title
        val t = "Another Band - Some Song Für Dich (Instrumental)"
        val d = YtEndDetector("vid00000001", t, 200_000)
        d.update(Snapshot(STATE_PLAYING, 1_000, null, t, 199_000), 0)
        d.update(Snapshot(STATE_PLAYING, 198_000, null, t, 199_000), 198_000)
        d.update(Snapshot(STATE_PLAYING, 0, null, t, 199_000), 199_500)          // reset, still "our" metadata
        assertTrue(d.settled(Snapshot(STATE_PLAYING, 1_000, null, t, 20_000), 202_000))
    }

    // --- other YouTube versions: less metadata, graceful degradation ---------------------------------------------

    @Test
    fun mediaIdIsTheBestSignal() {
        val d = det()
        d.update(Snapshot(STATE_PLAYING, 1_000, "yt:abc123", "something else", 1_000), 0)
        assertEquals("id", d.seenBy)
    }

    @Test
    fun withoutATitleAFittingDurationIdentifiesTheVideo() {
        val d = det()
        assertFalse(d.settled(Snapshot(STATE_PLAYING, 1_000, null, "", 15_000), 0))      // ad: its own duration
        assertFalse(d.seen)
        d.update(Snapshot(STATE_PLAYING, 1_000, null, null, 356_000), 20_000)
        assertEquals("duration", d.seenBy)
        assertTrue(d.settled(null, 380_000))
    }

    @Test
    fun withoutAnyMetadataThePlayTimeIdentifiesTheVideo() {
        val d = det()
        val blind = Snapshot(STATE_PLAYING, 0, null, null, 0)
        var t = 0L
        while (t < 300_000) {                                    // ad + most of the video: not long enough yet
            assertFalse(d.update(blind, t))
            t += 1_000
        }
        assertFalse(d.seen)
        assertFalse(d.settled(null, t))                          // a gap between ad and video doesn't end it
        while (t < 360_000) {
            d.update(blind, t)
            t += 1_000
        }
        assertEquals("play time", d.seenBy)
        assertTrue(d.settled(Snapshot(STATE_STOPPED, 0, null, null, 0), t))
    }

    @Test
    fun pausedBlindSessionsDoNotCountAsPlayTime() {
        val d = det()
        d.update(Snapshot(STATE_PAUSED, 0, null, null, 0), 0)
        d.update(Snapshot(STATE_PAUSED, 0, null, null, 0), 400_000)
        assertFalse(d.seen)
        assertNull(d.seenBy)
    }

    @Test
    fun unrelatedTitlesNeverMatch() {
        val d = det()
        d.update(Snapshot(STATE_PLAYING, 1_000, null, "Buy Shoes Now", 355_000), 0)
        assertFalse(d.seen)
    }
}
