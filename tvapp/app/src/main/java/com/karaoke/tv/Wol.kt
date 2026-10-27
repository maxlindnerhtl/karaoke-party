package com.karaoke.tv

import android.util.Log
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Wake-on-LAN (SPEC section 9, offline screen): a magic packet (6 x 0xFF, then the MAC 16 times) broadcast on UDP
 * ports 9 and 7. Needs the PC on Ethernet with WoL enabled in the BIOS/NIC; Wi-Fi adapters usually can't wake.
 */
object Wol {
    /** "aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF" or "aabbccddeeff" -> 6 bytes; null if it isn't a MAC. */
    fun parseMac(mac: String): ByteArray? {
        val t = mac.trim()
        if (!Regex("[0-9a-fA-F:\\-. ]+").matches(t)) return null
        val hex = t.filter { it.isLetterOrDigit() }
        if (hex.length != 12) return null
        return ByteArray(6) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    }

    fun format(mac: ByteArray) = mac.joinToString(":") { "%02x".format(it) }

    fun magicPacket(mac: ByteArray): ByteArray {
        require(mac.size == 6)
        return ByteArray(6) { 0xFF.toByte() } + ByteArray(16 * 6) { mac[it % 6] }
    }

    /** Sends the packet from a background thread. [subnetBroadcast] e.g. "192.168.1.255" (the PC's subnet), optional. */
    fun send(mac: ByteArray, subnetBroadcast: String?) {
        Thread {
            val packet = magicPacket(mac)
            val targets = listOfNotNull("255.255.255.255", subnetBroadcast).distinct()
            try {
                DatagramSocket().use { s ->
                    s.broadcast = true
                    repeat(3) {                    // UDP may drop one; three tries cost nothing
                        for (host in targets) for (port in intArrayOf(9, 7)) {
                            s.send(DatagramPacket(packet, packet.size, InetAddress.getByName(host), port))
                        }
                    }
                }
                Log.i(TAG, "wol: magic packet for ${format(mac)} sent to $targets")
            } catch (e: Exception) {
                Log.w(TAG, "wol: sending failed: $e")
            }
        }.start()
    }

    /** "192.168.1.20" -> "192.168.1.255" (assumes a /24 home network). */
    fun subnetBroadcast(ip: String?): String? {
        val p = ip?.split('.') ?: return null
        return if (p.size == 4) "${p[0]}.${p[1]}.${p[2]}.255" else null
    }
}
