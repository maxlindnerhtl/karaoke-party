package com.karaoke.tv

import org.json.JSONArray
import org.json.JSONObject

const val TAG = "Karaoke"

/** A queue entry as the server sends it (`state.entry_view`). */
data class Entry(
    val id: Int,
    val source: String,
    val songId: String?,
    val videoId: String?,
    val title: String,
    val artist: String,
    val duration: Double,
    val keyShift: Int,
    val vocalLevel: Int,
    val status: String,
    val error: String?,
    val singers: List<String>,
    val mediaUrl: String?,
    val lyricsUrl: String?,
    val levelsReady: List<Int>,
    val jobState: String?,
    val jobProgress: Double,
) {
    val isYoutube get() = source == "youtube"
    val singerText get() = singers.joinToString(" & ")
    val titleText get() = if (artist.isBlank()) title else "$title – $artist"

    /** Media URL of the same render at another backing-vocal level (`<song>_k+n_v<level>.m4a`). */
    fun mediaUrlFor(level: Int) = "/media/${songId}_k${if (keyShift >= 0) "+" else ""}${keyShift}_v$level.m4a"

    companion object {
        fun from(o: JSONObject): Entry {
            val job = o.optJSONObject("job")
            return Entry(
                id = o.getInt("id"),
                source = o.getString("source"),
                songId = o.optStringOrNull("song_id"),
                videoId = o.optStringOrNull("video_id"),
                title = o.optString("title"),
                artist = o.optString("artist"),
                duration = o.optDouble("duration", 0.0),
                keyShift = o.optInt("key_shift"),
                vocalLevel = o.optInt("vocal_level"),
                status = o.optString("status"),
                error = o.optStringOrNull("error"),
                singers = o.optJSONArray("singers").objects().map { it.optString("name", "?") },
                mediaUrl = o.optStringOrNull("media_url"),
                lyricsUrl = o.optStringOrNull("lyrics_url"),
                levelsReady = o.optJSONArray("vocal_levels_ready").ints(),
                jobState = job?.optString("state"),
                jobProgress = job?.optDouble("progress", 0.0) ?: 0.0,
            )
        }
    }
}

/** `GET /api/queue`, and the `hello` / `queue_changed` events. */
data class QueueState(val playing: Entry?, val playbackState: String, val playbackPosition: Double, val upcoming: List<Entry>) {
    /** What `/api/player/next` will pick: the head if it is ready, else the first ready entry. */
    val nextReady get() = upcoming.firstOrNull { it.status == "ready" }

    companion object {
        fun from(o: JSONObject): QueueState {
            val pb = o.optJSONObject("playback")
            return QueueState(
                playing = o.optJSONObject("playing")?.let(Entry::from),
                playbackState = pb?.optString("state") ?: "idle",
                playbackPosition = pb?.optDouble("position", 0.0) ?: 0.0,
                upcoming = o.optJSONArray("upcoming").objects().map(Entry::from),
            )
        }
    }
}

class Word(val start: Double, val end: Double, val text: String)

class Line(val start: Double, val end: Double, val text: String, val words: List<Word>)

/** `GET /api/lyrics/{song_id}`: times in seconds; the TV adds both offsets. */
class Lyrics(val source: String, val lines: List<Line>, var offsetMs: Int, var globalOffsetMs: Int) {
    companion object {
        fun from(o: JSONObject): Lyrics {
            val lines = o.optJSONArray("lines").objects().mapNotNull { l ->
                val start = l.optDouble("start", Double.NaN)
                val end = l.optDouble("end", Double.NaN)
                val text = l.optString("text").trim()
                if (start.isNaN() || text.isEmpty()) return@mapNotNull null
                var words = l.optJSONArray("words").objects().mapNotNull { w ->
                    val ws = w.optDouble("start", Double.NaN)
                    val we = w.optDouble("end", Double.NaN)
                    val wt = w.optString("text").trim()
                    if (wt.isEmpty()) null else Word(ws, we, wt)
                }
                val lineEnd = if (end.isNaN()) start + 3 else end
                if (words.isEmpty() || words.any { it.start.isNaN() || it.end.isNaN() }) {
                    words = evenWords(text, start, lineEnd)   // no word timing for this line: spread it evenly
                }
                Line(start, lineEnd, text, words)
            }
            return Lyrics(o.optString("source", "lrclib"), lines, o.optInt("offset_ms"), o.optInt("global_offset_ms"))
        }

        private fun evenWords(text: String, start: Double, end: Double): List<Word> {
            val parts = text.split(Regex("\\s+"))
            val step = (end - start) / parts.size
            return parts.mapIndexed { i, p -> Word(start + i * step, start + (i + 1) * step, p) }
        }
    }
}

fun JSONObject.optStringOrNull(key: String): String? = if (isNull(key)) null else optString(key)

fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

fun JSONArray?.ints(): List<Int> = if (this == null) emptyList() else (0 until length()).map { optInt(it) }
