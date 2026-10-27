package com.karaoke.tv

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.os.SystemClock
import android.view.Choreographer
import android.view.View
import com.karaoke.tv.Theme.Colors
import com.karaoke.tv.Theme.Space
import com.karaoke.tv.Theme.Text
import com.karaoke.tv.Theme.Timing
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

/**
 * Floating reactions (SPEC section 9): each guest reaction rises from the bottom of the right edge with the guest's
 * name under it and fades out. Drawn on one Canvas over the lobby and the player; animates only while any are visible.
 */
class ReactionsView(context: Context) : View(context) {
    private class Item(val emoji: String, val name: String, val start: Long, val x: Float, val phase: Float)

    private val items = ArrayList<Item>()
    private val emojiPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER }
    private val namePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        typeface = Theme.bold
        color = Colors.REACTION_NAME
        val r = context.px(Space.REACTION_SHADOW)
        setShadowLayer(r, 0f, r / 3, Colors.REACTION_SHADOW)
    }
    private var animating = false
    private val frame = object : Choreographer.FrameCallback {
        override fun doFrame(frameTimeNanos: Long) {
            invalidate()
            if (items.isNotEmpty()) Choreographer.getInstance().postFrameCallback(this) else animating = false
        }
    }

    init {
        isFocusable = false
    }

    fun add(emoji: String, name: String) {
        if (items.size >= Timing.REACTIONS_MAX) items.removeAt(0)
        val lane = Random.nextFloat()   // spread over the lane, so bursts don't stack
        items += Item(emoji, name, SystemClock.uptimeMillis(), lane, Random.nextFloat() * 2 * PI.toFloat())
        if (!animating) {
            animating = true
            Choreographer.getInstance().postFrameCallback(frame)
        }
    }

    override fun onDetachedFromWindow() {
        Choreographer.getInstance().removeFrameCallback(frame)
        animating = false
        super.onDetachedFromWindow()
    }

    private fun s(v: Float) = v * height / 1080f

    override fun onDraw(canvas: Canvas) {
        val now = SystemClock.uptimeMillis()
        items.removeAll { now - it.start > Timing.REACTION_MS }
        emojiPaint.textSize = s(Text.REACTION_EMOJI)
        namePaint.textSize = s(Text.REACTION_NAME)
        val laneLeft = width * Space.REACTION_LANE_LEFT
        val laneWidth = width - laneLeft - s(Space.SCREEN) - emojiPaint.textSize / 2
        for (it in items) {
            val p = (now - it.start).toFloat() / Timing.REACTION_MS          // 0 → 1
            val y = height - s(Space.SCREEN_BOTTOM) - p * height * Space.REACTION_RISE
            val x = laneLeft + it.x * laneWidth + sin(p * 4 * PI.toFloat() + it.phase) * s(Space.GAP_L)
            val fade = when {
                p < Timing.REACTION_FADE_IN -> p / Timing.REACTION_FADE_IN
                p > 1 - Timing.REACTION_FADE_OUT -> (1 - p) / Timing.REACTION_FADE_OUT
                else -> 1f
            }
            val alpha = (fade * 255).toInt().coerceIn(0, 255)
            emojiPaint.alpha = alpha
            namePaint.alpha = alpha
            canvas.drawText(it.emoji, x, y, emojiPaint)
            canvas.drawText(it.name, x, y + s(Space.REACTION_NAME_GAP) + namePaint.textSize, namePaint)
        }
    }
}
