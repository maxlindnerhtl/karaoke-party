package com.karaoke.tv

import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.karaoke.tv.yt.YouTubeApps
import com.karaoke.tv.yt.YtWatchService
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Runs on Android TV emulators in CI (API 24, 30, 34) after `scripts/tv_setup.py --grant-only` granted the
 * notification listener the way the README tells users to. YouTube itself isn't on the emulator images, so a
 * MediaSession published by this test stands in for it.
 */
@RunWith(AndroidJUnit4::class)
class YtWatchServiceTest {
    private val ctx: Context = InstrumentationRegistry.getInstrumentation().targetContext
    private val main = Handler(Looper.getMainLooper())
    private lateinit var server: MockWebServer
    private val ends = LinkedBlockingQueue<RecordedRequest>()
    private var session: MediaSession? = null

    @Before
    fun setUp() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path ?: ""
                if (path.startsWith("/api/player/ended") || path.startsWith("/api/player/skip")) ends.add(request)
                // only the player reports succeed; the lobby then simply shows "Server offline"
                return if (path.startsWith("/api/player/")) MockResponse().setBody("{}") else MockResponse().setResponseCode(404)
            }
        }
        server.start()
        Http.prefs(ctx).edit().putString("last", server.url("/").toString().trimEnd('/')).commit()
        YtWatchService.extraSessionPackages = setOf(ctx.packageName)
    }

    @After
    fun tearDown() {
        onMain { session?.release() }
        YtWatchService.extraSessionPackages = emptySet()
        server.shutdown()
    }

    @Test
    fun listenerGrantIsInPlace() {
        assertTrue("notification listener not granted on API ${Build.VERSION.SDK_INT}", YtWatchService.listenerEnabled(ctx))
        assertTrue(YtWatchService.canReturnByItself(ctx))
    }

    @Test
    fun grantCommandMatchesTheAndroidVersion() {
        val cmd = YtWatchService.grantCommands(ctx).single()
        val expected = if (Build.VERSION.SDK_INT >= 26) "cmd notification allow_listener" else "enabled_notification_listeners"
        assertTrue(cmd, expected in cmd)
        assertTrue(cmd, "${ctx.packageName}/com.karaoke.tv.yt.YtNotificationListener" in cmd)
    }

    @Test
    fun youtubeLinksDoNotCrashWithoutAYouTubeApp() {
        // emulator images have no YouTube app: launch() returns null or a browser, never throws
        YouTubeApps.launch(ctx, "vid00000001")
    }

    @Test
    fun endOfTheYouTubeSessionIsReported() {
        val title = "Example Artist - Example Song (Karaoke Version)"
        ActivityScenario.launch(MainActivity::class.java).use {
            onMain {
                session = MediaSession(ctx, "fake-youtube").apply {
                    setMetadata(MediaMetadata.Builder()
                        .putString(MediaMetadata.METADATA_KEY_TITLE, title)
                        .putLong(MediaMetadata.METADATA_KEY_DURATION, 30_000)
                        .build())
                    setPlaybackState(state(PlaybackState.STATE_PLAYING, 1_000))
                    isActive = true
                }
            }
            onMain { YtWatchService.start(ctx, entry(42, title, 30.0), null) }
            Thread.sleep(3_000)                                    // the watcher sees "our" video playing
            assertNotNull("the watch should still run", YtWatchService.active)
            onMain { session!!.setPlaybackState(state(PlaybackState.STATE_STOPPED, 29_500)) }

            val req = ends.poll(20, TimeUnit.SECONDS)
            assertNotNull("no end report within 20 s", req)
            assertEquals("/api/player/ended", req!!.path)
            assertEquals(42, JSONObject(req.body.readUtf8()).getInt("entry_id"))
            Log.i(TAG, "test: end reported on API ${Build.VERSION.SDK_INT}")
        }
    }

    private fun state(st: Int, pos: Long) = PlaybackState.Builder().setState(st, pos, 1f).build()

    private fun entry(id: Int, title: String, duration: Double) = Entry(
        id = id, source = "youtube", songId = null, videoId = "vid00000001", title = title, artist = "",
        duration = duration, keyShift = 0, vocalLevel = 0, status = "playing", error = null, singers = listOf("Test"),
        mediaUrl = null, lyricsUrl = null, levelsReady = emptyList(), jobState = null, jobProgress = 0.0,
    )

    private fun onMain(block: () -> Unit) {
        val done = CountDownLatch(1)
        main.post { try { block() } finally { done.countDown() } }
        done.await(10, TimeUnit.SECONDS)
    }
}
