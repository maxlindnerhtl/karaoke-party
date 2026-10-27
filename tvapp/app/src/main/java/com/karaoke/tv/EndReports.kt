package com.karaoke.tv

import android.content.Context
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * `ended` / `skip` reports that didn't reach the server (it was down when the song ended) are kept, also across an
 * app restart, and sent again when the app reconnects. Without this the server keeps the finished entry as playing:
 * the lobby offers it again as "Paused" and the party stops (found in the phase 5 party test). Main thread only.
 */
object EndReports {
    data class Report(val entryId: Int, val event: String, val position: Double) {
        fun toJson(): JSONObject = JSONObject().put("entry_id", entryId).put("event", event).put("position", position)

        companion object {
            fun from(o: JSONObject) = Report(o.getInt("entry_id"), o.getString("event"), o.optDouble("position", 0.0))
        }
    }

    /** Network error or server error: try again later. 200 = done; 409 = the server already ended it (e.g. host skip). */
    fun shouldRetry(code: Int) = code == -1 || code >= 500

    fun encode(reports: List<Report>): String = JSONArray().apply { reports.forEach { put(it.toJson()) } }.toString()

    fun decode(s: String?): List<Report> = runCatching {
        val a = JSONArray(s ?: "[]")
        (0 until a.length()).map { Report.from(a.getJSONObject(it)) }
    }.getOrDefault(emptyList())

    /** Adds [r], replacing an earlier report for the same entry. */
    fun merge(reports: List<Report>, r: Report) = reports.filter { it.entryId != r.entryId } + r

    private const val KEY = "pending_end_reports"

    private fun load(ctx: Context) = decode(Http.prefs(ctx).getString(KEY, null))

    private fun store(ctx: Context, reports: List<Report>) {
        Http.prefs(ctx).edit().putString(KEY, encode(reports)).apply()
    }

    fun isPending(ctx: Context, entryId: Int) = load(ctx).any { it.entryId == entryId }

    /** Reports the end of [entryId]; kept for [flush] if the server can't be reached. */
    fun send(ctx: Context, base: String?, entryId: Int, event: String, position: Double) {
        val r = Report(entryId, event, position)
        val app = ctx.applicationContext
        if (base == null) {
            store(app, merge(load(app), r))
            return
        }
        post(app, base, r)
    }

    /** Sends every kept report again (on reconnect). */
    fun flush(ctx: Context, base: String) {
        val app = ctx.applicationContext
        load(app).forEach { post(app, base, it) }
    }

    private fun post(app: Context, base: String, r: Report) {
        store(app, merge(load(app), r))   // kept until the server answers
        Http.post("$base/api/player/${r.event}", JSONObject().put("entry_id", r.entryId).put("position", r.position)) { code, _ ->
            if (shouldRetry(code)) {
                Log.w(TAG, "end report for entry ${r.entryId} (${r.event}) not delivered ($code); sending again on reconnect")
            } else {
                store(app, load(app).filter { it != r })
                if (code != 200) Log.i(TAG, "end report for entry ${r.entryId}: server answered $code (already ended)")
            }
        }
    }
}
