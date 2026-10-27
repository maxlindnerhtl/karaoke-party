package com.karaoke.tv

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Handler
import android.os.Looper
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.style.AbsoluteSizeSpan
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import com.karaoke.tv.Theme.Colors
import com.karaoke.tv.Theme.Space
import com.karaoke.tv.Theme.Text
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Request
import okhttp3.Response
import java.io.IOException

/**
 * Lobby (SPEC section 9): QR code + URL on the left; the banner card (up next / countdown / paused / YouTube) and
 * the queue as cards on the right, with the same badges as the phone page.
 */
class LobbyView(context: Context) : LinearLayout(context) {
    private val qrSize = context.pxi(Space.QR)
    private val qr = ImageView(context).apply { scaleType = ImageView.ScaleType.CENTER }
    private val url = context.label(Text.URL, bold = true)
    private val banner = context.label(Text.BANNER, bold = true, lines = 3)
    private val list = LinearLayout(context).apply { orientation = VERTICAL }
    private val footer = context.label(Text.WARNING, Colors.WARN, lines = 2)
    private val main = Handler(Looper.getMainLooper())
    private var qrFor: String? = null
    private var bannerText: String? = null

    init {
        orientation = HORIZONTAL
        setBackgroundColor(Colors.BG)
        val pad = context.pxi(Space.SCREEN)
        setPadding(pad, pad, pad, context.pxi(Space.SCREEN_BOTTOM))

        val qrBox = qrSize + 2 * context.pxi(Space.QR_MARGIN)
        val left = LinearLayout(context).apply {
            orientation = VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            addView(context.label(Text.LOBBY_TITLE, Colors.ACCENT, bold = true).apply { text = "KARAOKE" })
            addView(View(context), LayoutParams(0, 0, 1f))
            addView(qr, LayoutParams(qrBox, qrBox))
            addView(url, wrap().apply { topMargin = context.pxi(Space.GAP_L) })
            addView(context.label(Text.HINT, Colors.MUTED, lines = 2).apply {
                text = "Scan with your phone, enter your name,\npick a song."
                gravity = Gravity.CENTER
            }, wrap().apply { topMargin = context.pxi(Space.GAP_M) })
            addView(View(context), LayoutParams(0, 0, 1f))
        }
        qr.background = context.roundRect(Colors.QR_BG)
        addView(left, LayoutParams(context.pxi(Space.LOBBY_LEFT_WIDTH), LayoutParams.MATCH_PARENT))

        banner.background = context.cardBackground()
        banner.setPadding(context.pxi(Space.BANNER_PAD_H), context.pxi(Space.BANNER_PAD_V),
            context.pxi(Space.BANNER_PAD_H), context.pxi(Space.BANNER_PAD_V))
        banner.setLineSpacing(context.px(Space.GAP_S), 1f)
        val right = LinearLayout(context).apply {
            orientation = VERTICAL
            setPadding(context.pxi(Space.QUEUE_INDENT), 0, 0, 0)
            addView(banner, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
            addView(context.label(Text.SECTION, Colors.MUTED, bold = true).apply {
                text = "QUEUE"
                letterSpacing = 0.06f
            }, wrap().apply { topMargin = context.pxi(Space.GAP_XL); leftMargin = context.pxi(Space.GAP_S) })
            addView(list, LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f).apply { topMargin = context.pxi(Space.GAP_M) })
            addView(footer)
        }
        addView(right, LayoutParams(0, LayoutParams.MATCH_PARENT, 1f))
        setBanner("Looking for the karaoke server…")
    }

    private fun wrap() = LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT)

    fun setServer(base: String) {
        url.text = base.removePrefix("http://")
        if (qrFor == base) return
        qrFor = base
        Http.client.newCall(Request.Builder().url("$base/qr.png").build()).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                qrFor = null
            }

            override fun onResponse(call: Call, response: Response) {
                val bytes = response.use { it.body?.bytes() } ?: return
                val src = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return
                // 1 px per module from the server: scale up by a whole factor without filtering, so it stays sharp
                val k = maxOf(1, qrSize / src.width)
                val big = Bitmap.createScaledBitmap(src, src.width * k, src.height * k, false)
                main.post { qr.setImageBitmap(big) }
            }
        })
    }

    /** First line = what happens (TEXT), further lines = countdown / keys (ACCENT, smaller). */
    fun setBanner(text: String) {
        if (bannerText == text) return
        bannerText = text
        val cut = text.indexOf('\n')
        banner.text = if (cut < 0) text else SpannableStringBuilder(text).apply {
            setSpan(ForegroundColorSpan(Colors.ACCENT), cut + 1, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            setSpan(AbsoluteSizeSpan(context.pxi(Text.BANNER_SUB)), cut + 1, length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
    }

    fun setWarning(text: String?) {
        footer.text = text ?: ""
        footer.visibility = if (text.isNullOrEmpty()) View.GONE else View.VISIBLE
    }

    fun setQueue(entries: List<Entry>) {
        list.removeAllViews()
        if (entries.isEmpty()) {
            list.addView(context.label(Text.QUEUE_EMPTY, Colors.MUTED).apply { text = "Nothing queued yet." })
            return
        }
        val shown = if (entries.size > Space.QUEUE_CARDS) entries.take(Space.QUEUE_CARDS - 1) else entries
        shown.forEachIndexed { i, e -> list.addView(card(i, e)) }
        if (entries.size > shown.size) {
            list.addView(context.label(Text.QUEUE_MORE, Colors.MUTED).apply { text = "+ ${entries.size - shown.size} more" },
                wrap().apply { leftMargin = context.pxi(Space.CARD_PAD_H) })
        }
    }

    /** One queue entry, like a phone queue card: number, title, "singers · artist · key", source + status badges. */
    private fun card(i: Int, e: Entry) = LinearLayout(context).apply {
        orientation = HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        background = if (i == 0) context.cardBackground(Colors.CARD_NEXT_BORDER, Space.CARD_NEXT_BORDER)
            else context.cardBackground()
        setPadding(context.pxi(Space.CARD_PAD_H), context.pxi(Space.CARD_PAD_V),
            context.pxi(Space.CARD_PAD_H), context.pxi(Space.CARD_PAD_V))
        addView(context.label(Text.QUEUE_NUM, Colors.MUTED, medium = true).apply { text = "${i + 1}" },
            LayoutParams(context.pxi(Space.QUEUE_NUM_WIDTH), LayoutParams.WRAP_CONTENT))
        val meta = LinearLayout(context).apply {
            orientation = VERTICAL
            addView(context.label(Text.QUEUE_TITLE, medium = true).apply { text = e.title })
            addView(context.label(Text.QUEUE_DETAIL, Colors.MUTED).apply { text = detail(e) },
                LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { topMargin = context.pxi(Space.GAP_S) })
        }
        addView(meta, LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f))
        val gap = context.pxi(Space.GAP_M)
        addView(context.badge(if (e.isYoutube) "YouTube" else "Library", if (e.isYoutube) Colors.YT else Colors.ACCENT),
            LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { leftMargin = gap })
        val (label, color) = status(e)
        addView(context.badge(label, color),
            LayoutParams(LayoutParams.WRAP_CONTENT, LayoutParams.WRAP_CONTENT).apply { leftMargin = context.pxi(Space.GAP_M) })
        layoutParams = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT).apply {
            bottomMargin = context.pxi(Space.CARD_GAP)
        }
    }

    private fun detail(e: Entry): String {
        val parts = mutableListOf(e.singerText)
        if (e.artist.isNotBlank()) parts += e.artist
        if (e.keyShift != 0) parts += "key %+d".format(e.keyShift)
        if (e.status == "failed") e.error?.let { parts += it.take(60) }
        return parts.joinToString(" · ")
    }

    /** Badge text and color, as `statusBadge` in web/common.js. */
    private fun status(e: Entry): Pair<String, Int> = when (e.status) {
        "ready" -> "Ready" to Colors.OK
        "failed" -> "Failed" to Colors.BAD
        "preparing" -> when (e.jobState) {
            "separating" -> "Removing vocals… ${(e.jobProgress * 100).toInt()}%"
            "lyrics" -> "Syncing lyrics…"
            "rendering" -> "Mixing…"
            else -> "Preparing…"
        } to Colors.WARN
        else -> "In line for processing" to Colors.MUTED
    }
}
