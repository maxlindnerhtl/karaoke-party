package com.karaoke.tv

import android.graphics.Color
import android.graphics.Typeface

/**
 * The TV app's whole look in one place: typeface, colors, font sizes and spacing. Views read everything from here,
 * so a redesign only changes this file (plus layout code for new elements).
 *
 * The palette is the phone/host pages' dark theme (`web/style.css`); the typeface is the same too (the web's
 * `system-ui` is Roboto on Android). Kept light for the 2015 TV: flat colors, one static gradient, short fades.
 *
 * Sizes and spacing are pixels of a 1920x1080 screen; [px] scales them to the real screen. On the 65" 1080p TV one
 * pixel is ~0.75 mm, so e.g. 64 px text has ~34 mm capitals (~0.65° at 3 m from the couch). Nothing guests need to
 * read is below 26 px.
 */
object Theme {
    // --- typeface ----------------------------------------------------------------------------------------------
    val typeface: Typeface = Typeface.SANS_SERIF
    val regular: Typeface = Typeface.create(typeface, Typeface.NORMAL)
    val medium: Typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)   // the web's 600-650 titles
    val bold: Typeface = Typeface.create(typeface, Typeface.BOLD)

    // --- colors ------------------------------------------------------------------------------------------------
    object Colors {
        // web/style.css, dark theme
        val BG = Color.rgb(0x12, 0x11, 0x18)
        val PANEL = Color.rgb(0x1D, 0x1B, 0x26)
        val PANEL_2 = Color.rgb(0x28, 0x25, 0x34)
        val TEXT = Color.rgb(0xF1, 0xEF, 0xF8)
        val MUTED = Color.rgb(0x9F, 0x9B, 0xB0)
        val LINE = Color.rgb(0x34, 0x30, 0x3F)
        val ACCENT = Color.rgb(0x9D, 0x6B, 0xFF)
        val ACCENT_TEXT = Color.rgb(0x12, 0x0A, 0x22)
        val OK = Color.rgb(0x4F, 0xD1, 0x8B)
        val WARN = Color.rgb(0xF0, 0xB0, 0x4A)
        val BAD = Color.rgb(0xFF, 0x6B, 0x76)
        val YT = Color.rgb(0xFF, 0x5A, 0x5A)

        val DIM = MUTED
        val READY = OK
        val FAILED = BAD
        val QR_BG = Color.WHITE
        val TOAST_BG = 0xF2282534.toInt()                 // PANEL_2
        val SCRIM = 0xF0121118.toInt()                    // BG; offline screen over the lobby
        val BUTTON_BG = PANEL_2
        val BUTTON_TEXT = TEXT
        val BUTTON_FOCUSED_BG = ACCENT                    // the D-pad focus must be obvious
        val BUTTON_FOCUSED_TEXT = ACCENT_TEXT

        // cards (lobby queue, player "up next", stats)
        val CARD_BG = PANEL
        val CARD_BORDER = LINE
        val CARD_NEXT_BORDER = ACCENT                     // the entry that is sung next

        // player
        /** Static background, top to bottom: a hint of the accent at the top, fading into BG. */
        val PLAYER_GRADIENT = intArrayOf(Color.rgb(0x1E, 0x17, 0x33), BG, Color.rgb(0x0E, 0x0D, 0x13))
        val CHIP_BG = PANEL
        val CHIP_BORDER = LINE
        val PROGRESS_TRACK = PANEL_2
        val PROGRESS_FILL = ACCENT
        val NEXT_UP = 0x999F9BB0.toInt()                  // MUTED at 60 %: present, but not competing with the lyrics

        // lyrics
        val SUNG = Color.rgb(0xB4, 0x8C, 0xFF)          // a lighter ACCENT: as bright as the unsung text (≈7:1 on BG)
        val UNSUNG = TEXT
        val NEXT_LINE = MUTED
        val PREV_LINE = 0x8C9F9BB0.toInt()                // MUTED at 55 %
        val LYRICS_MESSAGE = MUTED
        val DOT_OFF = 0x33FFFFFF

        /** Duets: singer 1, singer 2 (sung words, name labels, header chip names). */
        val DUET = intArrayOf(SUNG, WARN)

        /** Duets: the next line, dimmed in its singer's color. */
        val DUET_NEXT = intArrayOf(Color.rgb(0x85, 0x6E, 0xB3), Color.rgb(0xA8, 0x83, 0x48))

        /** Duets: the previous line, fainter still. */
        val DUET_PREV = intArrayOf(0x8C856EB3.toInt(), 0x8CA88348.toInt())

        val REACTION_NAME = Color.WHITE
        val REACTION_SHADOW = Color.BLACK                // keeps names readable over the white QR code
        val STATS_RANK = ACCENT
    }

    // --- font sizes (1080p px) ---------------------------------------------------------------------------------
    object Text {
        // lobby
        const val LOBBY_TITLE = 64f
        const val URL = 40f
        const val HINT = 30f
        const val BANNER = 46f               // first banner line; the second (countdown, keys) is BANNER_SUB
        const val BANNER_SUB = 36f
        const val SECTION = 28f              // "QUEUE", uppercase like the web's .section-title
        const val QUEUE_NUM = 34f
        const val QUEUE_TITLE = 38f
        const val QUEUE_DETAIL = 28f
        const val BADGE = 26f
        const val QUEUE_EMPTY = 38f
        const val QUEUE_MORE = 30f
        const val WARNING = 28f

        // player
        const val SINGERS = 48f              // the singer chip
        const val SONG_INFO = 32f
        const val NEXT_UP = 28f              // the slim "Next · …" line at the bottom
        const val OFFLINE_NOTE = 28f
        const val LYRIC_MAIN = 110f          // current line; wraps to 2 rows before it shrinks
        const val LYRIC_MAIN_MIN = 64f       // never smaller (readable from the couch)
        const val LYRIC_NEXT = 64f
        const val LYRIC_NEXT_MIN = 46f
        const val LYRIC_PREV = 52f
        const val LYRIC_PREV_MIN = 40f
        const val LYRIC_MESSAGE = 64f
        const val DUET_LABEL = 36f

        // overlays
        const val TOAST = 44f
        const val OFFLINE_TITLE = 80f
        const val OFFLINE_DETAIL = 36f
        const val BUTTON = 36f
        const val DIALOG_INPUT = 40f
        const val REACTION_EMOJI = 96f
        const val REACTION_NAME = 28f

        // stats
        const val STATS_TITLE = 64f
        const val STATS_SUBTITLE = 32f
        const val STATS_HEADER = 40f
        const val STATS_ROW = 36f
        const val STATS_DETAIL = 26f
    }

    // --- spacing (1080p px) ------------------------------------------------------------------------------------
    object Space {
        const val SCREEN = 60f               // outer padding of full screens
        const val SCREEN_BOTTOM = 40f
        const val GAP_S = 6f
        const val GAP_M = 16f
        const val GAP_L = 24f
        const val GAP_XL = 50f

        // shapes (web: --radius 14px on a phone; scaled up for the TV)
        const val CARD_RADIUS = 20f
        const val CARD_BORDER = 2f
        const val CARD_NEXT_BORDER = 4f
        const val CARD_PAD_H = 26f
        const val CARD_PAD_V = 12f
        const val CARD_GAP = 12f
        const val PILL_RADIUS = 999f         // badges, chips, buttons
        const val BADGE_PAD_H = 14f
        const val BADGE_PAD_V = 4f
        const val BADGE_BORDER = 2f

        // lobby
        const val LOBBY_LEFT_WIDTH = 560f
        const val QR = 440f
        const val QR_MARGIN = 24f            // white quiet zone around the QR code
        const val QUEUE_INDENT = 50f
        const val QUEUE_NUM_WIDTH = 58f
        const val QUEUE_CARDS = 6            // cards shown; with more entries, one less plus "+ n more"
        const val BANNER_PAD_H = 32f
        const val BANNER_PAD_V = 24f

        // player
        const val PLAYER_TOP = 40f
        const val PLAYER_BOTTOM = 40f
        const val PLAYER_SIDE = 80f
        const val CHIP_PAD_H = 34f
        const val CHIP_PAD_V = 10f
        const val PROGRESS_Y = 196f          // top of the progress bar
        const val PROGRESS_WIDTH = 1152f
        const val PROGRESS_HEIGHT = 6f
        const val NEXT_UP_ENTRIES = 2        // entries in the "Next" line
        const val NEXT_UP_TITLE_CHARS = 36   // longer titles are cut with "…" so both entries stay visible
        const val LYRICS_MARGIN = 80f        // left/right margin of the lyric lines
        const val LYRICS_TOP = 220f          // nothing of the lyrics above this (header + progress bar)
        const val LYRICS_CENTER = 0.56f      // vertical center of the current line, fraction of the height
        const val LYRIC_ROW_HEIGHT = 1.2f    // row height, multiple of the font size
        const val LYRIC_NEXT_GAP = 40f
        const val LYRIC_PREV_GAP = 16f       // between the previous line and the dots/label zone
        const val DUET_LABEL_GAP = 18f
        const val DOT_RADIUS = 16f
        const val DOT_SPACING = 60f
        const val DOT_GAP = 50f              // dots above the line (their space is kept free for the previous line)

        // overlays
        const val TOAST_PAD = 28f
        const val TOAST_RADIUS = 24f
        const val TOAST_BOTTOM = 180f
        const val BUTTON_PAD_H = 44f
        const val BUTTON_PAD_V = 18f
        const val CORNER_NOTE = 30f
        const val REACTION_LANE_LEFT = 0.80f // reactions rise in the right 20 % of the screen
        const val REACTION_RISE = 0.55f      // how far up they float, fraction of the height
        const val REACTION_NAME_GAP = 8f
        const val REACTION_SHADOW = 6f       // blur radius; offset is a third of it

        // stats
        const val STATS_COLUMN_GAP = 40f
        const val STATS_CARD_PAD = 36f
        const val STATS_ROW = 14f
        const val STATS_RANK_WIDTH = 64f
    }

    // --- timing ------------------------------------------------------------------------------------------------
    object Timing {
        const val TOAST_MS = 2_500L
        const val FADE_MS = 200L               // lobby <-> player, stats
        const val REACTION_MS = 3_500L
        const val REACTIONS_MAX = 15
        const val REACTION_FADE_IN = 0.1f      // fractions of REACTION_MS
        const val REACTION_FADE_OUT = 0.3f
        const val STATS_MS = 30_000L
    }
}
