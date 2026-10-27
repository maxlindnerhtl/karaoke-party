package com.karaoke.tv

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Finds the server (UDP broadcast discovery, then the manual and last-known addresses), keeps the `/ws` WebSocket
 * open, and reconnects with backoff (1, 2, 4, 8, 10 s; discovery again after 3 failures). Listener calls run on the
 * main thread.
 */
class ServerClient(ctx: Context, private val listener: Listener) {
    interface Listener {
        fun onConnected(base: String)
        fun onOffline(detail: String, retryInSec: Int)
        fun onEvent(msg: JSONObject)
    }

    private val prefs = Http.prefs(ctx)
    private val main = Handler(Looper.getMainLooper())
    private val exec = Executors.newSingleThreadExecutor()
    private val probe = Http.client.newBuilder().callTimeout(3, TimeUnit.SECONDS).build()
    private var ws: WebSocket? = null
    private var generation = 0
    private var failures = 0
    private var running = false

    var base: String? = null
        private set
    val connected get() = ws != null && failures == 0

    fun start() {
        if (running) return
        running = true
        connect(rediscover = true)
    }

    fun stop() {
        running = false
        generation++
        main.removeCallbacksAndMessages(null)
        ws?.close(1000, null)
        ws = null
    }

    /** Manual IP entry from the offline screen: "192.168.1.20" or "192.168.1.20:8765". */
    fun setManual(addr: String) {
        val a = addr.trim().removePrefix("http://").trimEnd('/')
        if (a.isEmpty()) return
        val url = "http://" + if (':' in a) a else "$a:$DEFAULT_PORT"
        prefs.edit().putString("manual", url).apply()
        main.removeCallbacksAndMessages(null)
        ws?.cancel()
        ws = null
        failures = 0
        connect(rediscover = false, prefer = url)
    }

    private fun connect(rediscover: Boolean, prefer: String? = null) {
        val gen = ++generation
        val known = base
        exec.execute {
            val candidates = buildList {
                prefer?.let(::add)
                if (rediscover || known == null) discover()?.let(::add)
                known?.let(::add)
                prefs.getString("manual", null)?.let(::add)
                prefs.getString("last", null)?.let(::add)
            }.distinct()
            val found = candidates.firstOrNull(::reachable)
            main.post {
                if (gen != generation || !running) return@post
                if (found == null) failed("No karaoke server found on the network") else open(found, gen)
            }
        }
    }

    private fun open(url: String, gen: Int) {
        base = url
        prefs.edit().putString("last", url).apply()
        val req = Request.Builder().url(url.replaceFirst("http", "ws") + "/ws").build()
        ws = Http.client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                main.post {
                    if (gen != generation) return@post
                    failures = 0
                    Log.i(TAG, "connected to $url")
                    listener.onConnected(url)
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val msg = runCatching { JSONObject(text) }.getOrNull() ?: return
                main.post { if (gen == generation) listener.onEvent(msg) }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
                lost(gen, "Server closed the connection")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                lost(gen, "Connection lost (${t.javaClass.simpleName})")
            }
        })
    }

    private fun lost(gen: Int, detail: String) {
        main.post { if (gen == generation && running) failed(detail) }
    }

    private fun failed(detail: String) {
        generation++   // ignore late callbacks of the dead socket
        ws = null
        failures++
        val delay = minOf(MAX_RETRY_SEC, 1 shl minOf(failures - 1, 5))
        Log.w(TAG, "offline: $detail; retry in ${delay}s (failure $failures)")
        listener.onOffline(detail, delay)
        main.postDelayed({ if (running) connect(rediscover = failures >= 3) }, delay * 1000L)
    }

    private fun reachable(url: String): Boolean = runCatching {
        probe.newCall(Request.Builder().url("$url/api/settings").build()).execute().use { it.isSuccessful }
    }.getOrDefault(false)

    /** UDP broadcast to the discovery port; the reply's source address is the server. */
    private fun discover(): String? {
        try {
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 1500
                val req = "KARAOKE_DISCOVER".toByteArray()
                val buf = ByteArray(512)
                repeat(3) {
                    s.send(DatagramPacket(req, req.size, InetAddress.getByName("255.255.255.255"), DISCOVERY_PORT))
                    try {
                        val p = DatagramPacket(buf, buf.size)
                        s.receive(p)
                        val o = JSONObject(String(p.data, 0, p.length))
                        if (o.optString("service") == "karaoke") {
                            val url = "http://${p.address.hostAddress}:${o.optInt("port", DEFAULT_PORT)}"
                            Log.i(TAG, "discovered $url")
                            return url
                        }
                    } catch (_: SocketTimeoutException) {
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "discovery failed: $e")
        }
        return null
    }

    companion object {
        const val DISCOVERY_PORT = 8766
        const val DEFAULT_PORT = 8765

        /** A LAN probe is cheap; a longer wait only keeps the TV offline after the server is back (phase 5). */
        const val MAX_RETRY_SEC = 10
    }
}
