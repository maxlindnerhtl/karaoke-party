package com.karaoke.tv

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.view.Choreographer
import android.view.View
import com.karaoke.tv.Theme.Colors
import com.karaoke.tv.Theme.Space
import com.karaoke.tv.Theme.Text
import kotlin.math.ceil

/**
 * Fullscreen lyrics on one Canvas (SPEC section 9): the line to sing with word-level highlighting, the previous line
 * faded above it and the next line below it, plus the thin song progress bar under the header. Redrawn every frame
 * from [timeMs] / [durationMs] (the ExoPlayer position and duration) while [start]ed.
 *
 * Duets ([duet] = the two singer names): lines alternate between the singers, starting with the first one. Each line
 * is highlighted in its singer's color, with the singer's name above it.
 *
 * Sizes, colors and spacing come from [Theme] (1080p pixels, scaled to the real height).
 */
class LyricsView(context: Context) : View(context) {
    var lyrics: Lyrics? = null
        set(value) {
            field = value
            layouts.clear()
        }

    /** Shown instead of lyrics (e.g. "Loading lyrics…"), or null. */
    var message: String? = null

    /** The two singer names of a duet, or null for one singer. */
    var duet: List<String>? = null

    var timeMs: () -> Long = { 0L }

    /** Song length for the progress bar; ≤ 0 while unknown (no bar). */
    var durationMs: () -> Long = { 0L }

    private var running = false
    private val frame = object : Choreographer.FrameCallback {
        override fun doFrame(frameTimeNanos: Long) {
            if (!running) return
            invalidate()
            Choreographer.getInstance().postFrameCallback(this)
        }
    }

    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Theme.bold }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Theme.bold
        textAlign = Paint.Align.CENTER
    }
    private val dotPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val bar = RectF()

    /** Layout per (line index, slot); slot 0 = current line (up to 2 rows), 1 = next line, 2 = previous line (1 row). */
    private val layouts = HashMap<Long, Placed>()

    private class Placed(val fit: LyricLayout.Fit, val words: List<Word>, val rowWidths: FloatArray, val wordWidths: FloatArray, val space: Float)

    fun start() {
        if (running) return
        running = true
        Choreographer.getInstance().postFrameCallback(frame)
    }

    fun stop() {
        running = false
        Choreographer.getInstance().removeFrameCallback(frame)
    }

    override fun onDetachedFromWindow() {
        stop()
        super.onDetachedFromWindow()
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) = layouts.clear()

    private fun s(v: Float) = v * height / 1080f

    /** Duet singer (0 or 1) of a line, or -1 when there is one singer. */
    private fun singerOf(line: Int) = if (duet?.size == 2) line % 2 else -1

    override fun onDraw(canvas: Canvas) {
        drawProgress(canvas)
        val lyr = lyrics
        val msg = message
        if (lyr == null || lyr.lines.isEmpty() || msg != null) {
            paint.textSize = s(Text.LYRIC_MESSAGE)
            paint.textScaleX = 1f
            paint.color = Colors.LYRICS_MESSAGE
            paint.textAlign = Paint.Align.CENTER
            canvas.drawText(msg ?: "♪", width / 2f, height * Space.LYRICS_CENTER, paint)
            paint.textAlign = Paint.Align.LEFT
            return
        }
        val t = (timeMs() - lyr.offsetMs - lyr.globalOffsetMs) / 1000.0
        val lines = lyr.lines
        val i = lines.indexOfLast { it.start <= t }

        // which line goes in the main slot, and is it only a preview (not sung yet)?
        var main = i
        var preview = false
        if (i < 0) {
            main = 0
            preview = true
        } else {
            val cur = lines[i]
            val nxt = lines.getOrNull(i + 1)
            if (nxt != null && nxt.start - cur.end > BREAK_S && t > cur.end + 0.5) {
                main = i + 1      // instrumental break: show what comes next
                preview = true
            } else if (nxt == null && t > cur.end + 2) {
                main = -1         // lyrics are over
            }
        }
        if (main < 0) return

        val avail = width - 2 * s(Space.LYRICS_MARGIN)
        val p = place(main, 0, avail)
        val rowH = p.fit.size * Space.LYRIC_ROW_HEIGHT
        val blockTop = height * Space.LYRICS_CENTER - rowH * p.fit.rows.size / 2
        val singer = singerOf(main)
        drawLine(canvas, p, blockTop, if (preview) -1.0 else t, if (singer >= 0) Colors.DUET[singer] else Colors.SUNG, null)

        var aboveTop = blockTop                       // the label, the dots and the previous line stack above the line
        if (singer >= 0) {
            labelPaint.textSize = s(Text.DUET_LABEL)
            labelPaint.color = Colors.DUET[singer]
            val baseline = blockTop - s(Space.DUET_LABEL_GAP)
            canvas.drawText(duet!![singer], width / 2f, baseline, labelPaint)
            aboveTop = baseline + labelPaint.fontMetrics.ascent
        }

        val dotsY = aboveTop - s(Space.DOT_GAP)
        if (preview) {   // countdown dots: 3 → 1 in the last 3 s before the line
            val remain = lines[main].start - t
            if (remain in 0.0..COUNTDOWN_S) {
                val n = ceil(remain).toInt().coerceIn(1, 3)
                for (d in 0 until 3) {
                    dotPaint.color = if (d < n) (if (singer >= 0) Colors.DUET[singer] else Colors.SUNG) else Colors.DOT_OFF
                    canvas.drawCircle(width / 2f + (d - 1) * s(Space.DOT_SPACING), dotsY, s(Space.DOT_RADIUS), dotPaint)
                }
            }
        }

        if (main > 0) {   // the previous line, faded, above the dots' place (kept free so nothing jumps)
            val pp = place(main - 1, 2, avail)
            val prevBottom = dotsY - s(Space.DOT_RADIUS) - s(Space.LYRIC_PREV_GAP)
            val prevTop = prevBottom - pp.fit.size * Space.LYRIC_ROW_HEIGHT
            val ps = singerOf(main - 1)
            if (prevTop >= s(Space.LYRICS_TOP)) {
                drawLine(canvas, pp, prevTop, null, 0, if (ps >= 0) Colors.DUET_PREV[ps] else Colors.PREV_LINE)
            }
        }

        if (main + 1 < lines.size) {
            val np = place(main + 1, 1, avail)
            val ns = singerOf(main + 1)
            drawLine(canvas, np, blockTop + rowH * p.fit.rows.size + s(Space.LYRIC_NEXT_GAP), null, 0,
                if (ns >= 0) Colors.DUET_NEXT[ns] else Colors.NEXT_LINE)
        }
    }

    /**
     * Draws the rows of [p] from [top]. [t] = lyric time for highlighting, -1 = nothing sung yet, null = the next line
     * (all in [plainColor]); [sungColor] = the wipe color.
     */
    private fun drawLine(canvas: Canvas, p: Placed, top: Float, t: Double?, sungColor: Int, plainColor: Int?) {
        paint.textSize = p.fit.size
        paint.textScaleX = p.fit.scaleX
        val rowH = p.fit.size * Space.LYRIC_ROW_HEIGHT
        val fm = paint.fontMetrics
        p.fit.rows.forEachIndexed { r, range ->
            val baseline = top + rowH * r + (rowH - (fm.descent - fm.ascent)) / 2 - fm.ascent
            var x = (width - p.rowWidths[r]) / 2
            for (wi in range) {
                val w = p.words[wi]
                val ww = p.wordWidths[wi]
                if (t == null) {
                    paint.color = plainColor ?: Colors.NEXT_LINE
                    canvas.drawText(w.text, x, baseline, paint)
                } else {
                    val frac = when {
                        t < 0 || t <= w.start -> 0.0
                        t >= w.end -> 1.0
                        else -> (t - w.start) / (w.end - w.start)
                    }
                    if (frac < 1.0) {
                        paint.color = Colors.UNSUNG
                        canvas.drawText(w.text, x, baseline, paint)
                    }
                    if (frac > 0.0) {   // wipe through the word as it is sung
                        canvas.save()
                        canvas.clipRect(x, baseline + fm.ascent, x + (ww * frac).toFloat(), baseline + fm.descent)
                        paint.color = sungColor
                        canvas.drawText(w.text, x, baseline, paint)
                        canvas.restore()
                    }
                }
                x += ww + p.space
            }
        }
    }

    /** Thin progress bar under the header (track + rounded fill), from the player position and duration. */
    private fun drawProgress(canvas: Canvas) {
        val dur = durationMs()
        if (dur <= 0) return
        val frac = (timeMs().toFloat() / dur).coerceIn(0f, 1f)
        val w = s(Space.PROGRESS_WIDTH)
        val h = s(Space.PROGRESS_HEIGHT)
        val left = (width - w) / 2
        val top = s(Space.PROGRESS_Y)
        bar.set(left, top, left + w, top + h)
        barPaint.color = Colors.PROGRESS_TRACK
        canvas.drawRoundRect(bar, h / 2, h / 2, barPaint)
        if (frac > 0f) {
            bar.right = left + maxOf(w * frac, h)
            barPaint.color = Colors.PROGRESS_FILL
            canvas.drawRoundRect(bar, h / 2, h / 2, barPaint)
        }
    }

    private fun place(index: Int, slot: Int, avail: Float): Placed =
        layouts.getOrPut(index * 3L + slot) {
            val line = lyrics!!.lines[index]
            val base = s(when (slot) { 0 -> Text.LYRIC_MAIN; 1 -> Text.LYRIC_NEXT; else -> Text.LYRIC_PREV })
            val min = s(when (slot) { 0 -> Text.LYRIC_MAIN_MIN; 1 -> Text.LYRIC_NEXT_MIN; else -> Text.LYRIC_PREV_MIN })
            paint.textScaleX = 1f
            paint.textSize = base
            val widths = FloatArray(line.words.size) { paint.measureText(line.words[it].text) }
            val fit = LyricLayout.fit(widths, paint.measureText(" "), avail, base, min, if (slot == 0) 2 else 1)
            // measure again at the final size and scaleX, so the wipe matches the drawn glyphs exactly
            paint.textSize = fit.size
            paint.textScaleX = fit.scaleX
            val ww = FloatArray(line.words.size) { paint.measureText(line.words[it].text) }
            val space = paint.measureText(" ")
            val rows = FloatArray(fit.rows.size) { LyricLayout.rowWidth(ww, space, fit.rows[it].first, fit.rows[it].last) }
            Placed(fit, line.words, rows, ww, space)
        }

    companion object {
        const val BREAK_S = 8.0       // SPEC: countdown dots after instrumental breaks longer than 8 s
        const val COUNTDOWN_S = 3.0
    }
}
