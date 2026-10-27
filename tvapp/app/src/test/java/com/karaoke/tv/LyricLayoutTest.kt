package com.karaoke.tv

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LyricLayoutTest {
    private fun words(vararg w: Float) = w

    @Test
    fun shortLineStaysOneRowAtBaseSize() {
        val f = LyricLayout.fit(words(100f, 200f, 100f), 20f, 1000f, 110f, 64f, 2)
        assertEquals(listOf(0 until 3), f.rows)
        assertEquals(110f, f.size)
        assertEquals(1f, f.scaleX)
    }

    @Test
    fun tooWideLineWrapsNearTheMiddleBeforeShrinking() {
        // 6 words of 200 + 5 spaces of 20 = 1300 > 1000: two rows of 3 words (640 each) at the base size
        val f = LyricLayout.fit(FloatArray(6) { 200f }, 20f, 1000f, 110f, 64f, 2)
        assertEquals(listOf(0 until 3, 3 until 6), f.rows)
        assertEquals(110f, f.size)
    }

    @Test
    fun breakBalancesUnevenWords() {
        // splitting after the 700-wide word vs the rest (100+20+100+20+400 = 640) beats any other split
        assertEquals(1, LyricLayout.balancedBreak(words(700f, 100f, 100f, 400f), 20f))
    }

    @Test
    fun wrappedRowsShrinkButNeverBelowTheMinimum() {
        // 2 rows of 6 words = 1880 wide: would need 53 % of 110 (58 px), below the 64 px floor
        val f = LyricLayout.fit(FloatArray(12) { 300f }, 20f, 1000f, 110f, 64f, 2)
        assertEquals(listOf(0 until 6, 6 until 12), f.rows)
        assertEquals(64f, f.size)
        val widestAtMin = (6 * 300f + 5 * 20f) * 64f / 110f
        assertEquals(1000f / widestAtMin, f.scaleX, 1e-4f)
        assertTrue(f.scaleX < 1f)
    }

    @Test
    fun nextLineIsOneRowAndShrinks() {
        val f = LyricLayout.fit(FloatArray(6) { 200f }, 20f, 1000f, 68f, 46f, 1)
        assertEquals(1, f.rows.size)
        assertEquals(68f * 1000f / 1300f, f.size, 1e-3f)
    }
}
