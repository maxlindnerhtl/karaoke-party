package com.karaoke.tv

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** Shared OkHttp client and small JSON REST helpers. Callbacks run on the main thread. */
object Http {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .pingInterval(10, TimeUnit.SECONDS)   // notices a dead server on the WebSocket
        .build()

    private val main = Handler(Looper.getMainLooper())
    private val JSON = "application/json".toMediaType()

    /** The server base URL the app last connected to, e.g. `http://192.168.1.20:8765`. The YouTube service uses it too. */
    fun base(ctx: Context): String? = prefs(ctx).getString("last", null)

    fun prefs(ctx: Context) = ctx.getSharedPreferences("karaoke", Context.MODE_PRIVATE)

    /** [cb] gets the HTTP status (-1 = network error) and the JSON body, if any. */
    fun post(url: String, body: JSONObject = JSONObject(), cb: ((Int, JSONObject?) -> Unit)? = null) =
        call(Request.Builder().url(url).post(body.toString().toRequestBody(JSON)).build(), cb)

    fun get(url: String, cb: (Int, JSONObject?) -> Unit) = call(Request.Builder().url(url).build(), cb)

    private fun call(req: Request, cb: ((Int, JSONObject?) -> Unit)?) {
        client.newCall(req).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                Log.w(TAG, "${req.method} ${req.url} failed: $e")
                main.post { cb?.invoke(-1, null) }
            }

            override fun onResponse(call: Call, response: Response) {
                val code = response.code
                val json = response.use { r -> runCatching { JSONObject(r.body!!.string()) }.getOrNull() }
                if (code >= 400) Log.w(TAG, "${req.method} ${req.url} -> $code ${json?.optString("detail")}")
                main.post { cb?.invoke(code, json) }
            }
        })
    }
}
