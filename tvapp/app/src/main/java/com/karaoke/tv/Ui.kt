package com.karaoke.tv

import android.content.Context
import android.graphics.drawable.GradientDrawable
import android.text.TextUtils
import android.util.TypedValue
import android.view.View
import android.widget.TextView
import com.karaoke.tv.Theme.Space

/** Pixels of a 1080p screen → real pixels (all sizes in [Theme] are given for 1920x1080). */
fun Context.px(v: Float): Float {
    val dm = resources.displayMetrics
    return v * minOf(dm.widthPixels, dm.heightPixels) / 1080f
}

fun Context.pxi(v: Float) = px(v).toInt()

/** A TextView in the theme's typeface. [sizePx] is a [Theme.Text] size; [medium] = the web's semi-bold titles. */
fun Context.label(sizePx: Float, color: Int = Theme.Colors.TEXT, bold: Boolean = false, lines: Int = 1,
                  medium: Boolean = false) = TextView(this).apply {
    setTextSize(TypedValue.COMPLEX_UNIT_PX, px(sizePx))
    setTextColor(color)
    typeface = if (bold) Theme.bold else if (medium) Theme.medium else Theme.regular
    maxLines = lines
    ellipsize = TextUtils.TruncateAt.END
    includeFontPadding = false
}

/** Rounded rectangle like the web's cards and pills; [radius], [strokeWidth] in 1080p px; stroke 0 = none. */
fun Context.roundRect(fill: Int, radius: Float = Space.CARD_RADIUS, stroke: Int = 0, strokeWidth: Float = Space.CARD_BORDER) =
    GradientDrawable().apply {
        setColor(fill)
        cornerRadius = px(radius)
        if (stroke != 0) setStroke(pxi(strokeWidth), stroke)
    }

/** A card background (web `.card`): PANEL fill, LINE border, or [border] for the highlighted card. */
fun Context.cardBackground(border: Int = Theme.Colors.CARD_BORDER, borderWidth: Float = Space.CARD_BORDER) =
    roundRect(Theme.Colors.CARD_BG, Space.CARD_RADIUS, border, borderWidth)

/** An outlined pill in [color] (web `.badge`). */
fun Context.badge(text: String, color: Int) = label(Theme.Text.BADGE, color, medium = true).apply {
    this.text = text
    setPadding(pxi(Space.BADGE_PAD_H), pxi(Space.BADGE_PAD_V), pxi(Space.BADGE_PAD_H), pxi(Space.BADGE_PAD_V))
    background = roundRect(0, Space.PILL_RADIUS, color, Space.BADGE_BORDER)
}

/** Shows this view with a short fade-in (cheap: one alpha animation, no layers kept). */
fun View.fadeIn() {
    if (visibility == View.VISIBLE && alpha == 1f) return
    animate().cancel()
    alpha = 0f
    visibility = View.VISIBLE
    animate().alpha(1f).setDuration(Theme.Timing.FADE_MS).start()
}
