package com.karaoke.tv

/**
 * How a lyric line fits the screen width. Pure logic (no Android types) so it can be unit-tested.
 *
 * Order (current line): one row at the base size if it fits; otherwise wrap onto two rows at the word boundary that
 * balances them best (near the middle), and only then scale the text down, never below the minimum size. If the
 * text is still too wide at the minimum size, it is condensed horizontally (scaleX) as a last resort, so no word is
 * ever cut off.
 */
object LyricLayout {
    class Fit(val rows: List<IntRange>, val size: Float, val scaleX: Float)

    /** Width of words [from, to] with single spaces between them. */
    fun rowWidth(widths: FloatArray, space: Float, from: Int, to: Int): Float {
        var w = 0f
        for (i in from..to) w += widths[i]
        return w + space * (to - from)
    }

    /** Index of the first word of the second row: the split that makes the wider of the two rows narrowest. */
    fun balancedBreak(widths: FloatArray, space: Float): Int {
        val n = widths.size
        require(n >= 2)
        var best = 1
        var bestWidth = Float.MAX_VALUE
        for (k in 1 until n) {
            val widest = maxOf(rowWidth(widths, space, 0, k - 1), rowWidth(widths, space, k, n - 1))
            if (widest < bestWidth) {
                bestWidth = widest
                best = k
            }
        }
        return best
    }

    /** [widths] and [space] are measured at [baseSize]; text width scales linearly with the size. */
    fun fit(widths: FloatArray, space: Float, avail: Float, baseSize: Float, minSize: Float, maxRows: Int): Fit {
        val n = widths.size
        if (n == 0) return Fit(emptyList(), baseSize, 1f)
        val single = rowWidth(widths, space, 0, n - 1)
        val rows = when {
            single <= avail -> listOf(0 until n)
            maxRows >= 2 && n >= 2 -> balancedBreak(widths, space).let { k -> listOf(0 until k, k until n) }
            else -> listOf(0 until n)
        }
        val widest = rows.maxOf { rowWidth(widths, space, it.first, it.last) }
        val size = (baseSize * avail / widest).coerceIn(minSize, baseSize)
        val scaleX = (avail / (widest * size / baseSize)).coerceAtMost(1f)
        return Fit(rows, size, scaleX)
    }
}
