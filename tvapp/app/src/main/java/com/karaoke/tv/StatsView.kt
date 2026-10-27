package com.karaoke.tv

import android.content.Context
import android.view.Gravity
import android.widget.LinearLayout
import com.karaoke.tv.Theme.Colors
import com.karaoke.tv.Theme.Space
import com.karaoke.tv.Theme.Text
import org.json.JSONObject

/** Stats screen (SPEC section 9, sent from the host page): most sung songs and top singers, from `show_stats`. */
class StatsView(context: Context) : LinearLayout(context) {
    private val subtitle = context.label(Text.STATS_SUBTITLE, Colors.MUTED)
    private val songs = LinearLayout(context).apply { orientation = VERTICAL }
    private val singers = LinearLayout(context).apply { orientation = VERTICAL }

    init {
        orientation = VERTICAL
        setBackgroundColor(Colors.BG)
        val pad = context.pxi(Space.SCREEN)
        setPadding(pad, pad, pad, context.pxi(Space.SCREEN_BOTTOM))
        isClickable = true   // keeps the lobby underneath from getting touches/focus
        addView(context.label(Text.STATS_TITLE, Colors.ACCENT, bold = true).apply { text = "Party stats" })
        addView(subtitle, wrap().apply { topMargin = context.pxi(Space.GAP_M) })
        val columns = LinearLayout(context).apply {
            orientation = HORIZONTAL
            addView(column("Most sung songs", songs), LayoutParams(0, LayoutParams.WRAP_CONTENT, 3f))
            addView(column("Top singers", singers), LayoutParams(0, LayoutParams.WRAP_CONTENT, 2f).apply {
                leftMargin = context.pxi(Space.STATS_COLUMN_GAP)
            })
        }
        addView(columns, LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f).apply { topMargin = context.pxi(Space.GAP_XL) })
        addView(context.label(Text.STATS_DETAIL, Colors.MUTED).apply {
            text = "OK or Back to close"
            gravity = Gravity.END
        }, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
    }

    private fun wrap() = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT)

    private fun column(title: String, rows: LinearLayout) = LinearLayout(context).apply {
        orientation = VERTICAL
        background = context.cardBackground()
        val pad = context.pxi(Space.STATS_CARD_PAD)
        setPadding(pad, pad, pad, pad - context.pxi(Space.STATS_ROW))
        addView(context.label(Text.STATS_HEADER, bold = true).apply { text = title })
        addView(rows, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
            topMargin = context.pxi(Space.GAP_L)
        })
    }

    /** Fills the screen from the `show_stats` payload: {songs: [{title, artist, source, plays}], singers: [{name, songs}], total_plays}. */
    fun show(stats: JSONObject) {
        val total = stats.optInt("total_plays")
        subtitle.text = if (total == 1) "1 song sung so far" else "$total songs sung so far"
        songs.removeAllViews()
        singers.removeAllViews()
        stats.optJSONArray("songs").objects().take(MAX_ROWS).forEachIndexed { i, s ->
            val artist = s.optString("artist")
            val title = s.optString("title") + if (artist.isBlank()) "" else " – $artist"
            songs.addView(row(i, title, plural(s.optInt("plays"), "time")))
        }
        stats.optJSONArray("singers").objects().take(MAX_ROWS).forEachIndexed { i, s ->
            singers.addView(row(i, s.optString("name"), plural(s.optInt("songs"), "song")))
        }
        if (songs.childCount == 0) songs.addView(context.label(Text.STATS_ROW, Colors.MUTED).apply { text = "No songs finished yet." })
    }

    private fun plural(n: Int, word: String) = if (n == 1) "1 $word" else "$n ${word}s"

    /** Rank (accent, the top 3 bold), name, and the count as a muted badge. */
    private fun row(i: Int, text: String, detail: String) = LinearLayout(context).apply {
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        addView(context.label(Text.STATS_ROW, if (i < 3) Colors.STATS_RANK else Colors.MUTED, bold = i < 3).apply {
            this.text = "${i + 1}"
        }, LayoutParams(context.pxi(Space.STATS_RANK_WIDTH), LayoutParams.WRAP_CONTENT))
        addView(context.label(Text.STATS_ROW, medium = i < 3).apply { this.text = text },
            LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        addView(context.badge(detail, Colors.MUTED),
            LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { leftMargin = context.pxi(Space.GAP_M) })
        layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
            bottomMargin = context.pxi(Space.STATS_ROW)
        }
    }

    companion object {
        const val MAX_ROWS = 10
    }
}
