package com.karaoke.tv

import com.karaoke.tv.EndReports.Report
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class EndReportsTest {
    @Test
    fun retriesOnlyWhenTheServerDidNotTakeIt() {
        assertTrue(EndReports.shouldRetry(-1))     // server down (the phase 5 case)
        assertTrue(EndReports.shouldRetry(502))
        assertFalse(EndReports.shouldRetry(200))
        assertFalse(EndReports.shouldRetry(409))   // already ended on the server, e.g. skipped by the host meanwhile
    }

    @Test
    fun keepsOneReportPerEntry() {
        val a = Report(30, "ended", 192.6)
        val b = Report(31, "skip", 12.0)
        val list = EndReports.merge(EndReports.merge(listOf(a), b), Report(30, "skip", 100.0))
        assertEquals(listOf(b, Report(30, "skip", 100.0)), list)
    }

    @Test
    fun survivesAnAppRestart() {
        val list = listOf(Report(30, "ended", 192.6), Report(31, "skip", 0.0))
        assertEquals(list, EndReports.decode(EndReports.encode(list)))
        assertEquals(emptyList<Report>(), EndReports.decode(null))
        assertEquals(emptyList<Report>(), EndReports.decode("not json"))
    }
}
