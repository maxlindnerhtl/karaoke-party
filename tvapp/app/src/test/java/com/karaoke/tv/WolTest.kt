package com.karaoke.tv

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class WolTest {
    private val mac = byteArrayOf(0x02, 0x00, 0x5e, 0x10, 0x00, 0x01)

    @Test
    fun parsesCommonMacFormats() {
        assertArrayEquals(mac, Wol.parseMac("02:00:5e:10:00:01"))
        assertArrayEquals(mac, Wol.parseMac("02-00-5E-10-00-01"))
        assertArrayEquals(mac, Wol.parseMac(" 02005e100001 "))
        assertNull(Wol.parseMac("02:00:5e:10:00"))
        assertNull(Wol.parseMac("hello world!"))
        assertEquals("02:00:5e:10:00:01", Wol.format(mac))
    }

    @Test
    fun magicPacketIsSixFfThenTheMacSixteenTimes() {
        val p = Wol.magicPacket(mac)
        assertEquals(102, p.size)
        for (i in 0 until 6) assertEquals(0xFF.toByte(), p[i])
        for (r in 0 until 16) assertArrayEquals(mac, p.copyOfRange(6 + r * 6, 12 + r * 6))
    }

    @Test
    fun subnetBroadcast() {
        assertEquals("192.168.1.255", Wol.subnetBroadcast("192.168.1.20"))
        assertNull(Wol.subnetBroadcast(null))
    }
}
