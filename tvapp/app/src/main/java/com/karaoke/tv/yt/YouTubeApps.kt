package com.karaoke.tv.yt

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build

/**
 * Which app plays YouTube entries. Android TV and Google TV ship `com.google.android.youtube.tv`; some boxes only
 * have the phone app, Fire TV has its own. Each needs a `<queries>` entry in the manifest (Android 11+ package
 * visibility). If none of them is installed, any app that opens youtube.com links is used.
 */
object YouTubeApps {
    /** Known YouTube apps, best first. */
    val PACKAGES = listOf(
        "com.google.android.youtube.tv",
        "com.amazon.firetv.youtube",
        "com.google.android.youtube",
    )

    fun watchUri(videoId: String): Uri = Uri.parse("https://www.youtube.com/watch?v=$videoId")

    /** The intent that opens [videoId] and the package it goes to, or null if no app can open it. */
    fun launch(ctx: Context, videoId: String): Pair<Intent, String>? {
        val view = Intent(Intent.ACTION_VIEW, watchUri(videoId)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        for (pkg in PACKAGES) {
            val intent = Intent(view).setPackage(pkg)
            if (resolves(ctx, intent)) return intent to pkg
        }
        val any = resolvePackage(ctx, view) ?: return null
        return Intent(view).setPackage(any) to any
    }

    /** Media sessions from these packages count as "YouTube" while [launched] plays our entry. */
    fun sessionPackages(launched: String?): Set<String> = PACKAGES.toSet() + listOfNotNull(launched)

    private fun resolves(ctx: Context, intent: Intent) = resolvePackage(ctx, intent) != null

    @Suppress("DEPRECATION")
    private fun resolvePackage(ctx: Context, intent: Intent): String? {
        val pm = ctx.packageManager
        val info = if (Build.VERSION.SDK_INT >= 33) {
            pm.resolveActivity(intent, PackageManager.ResolveInfoFlags.of(PackageManager.MATCH_DEFAULT_ONLY.toLong()))
        } else {
            pm.resolveActivity(intent, PackageManager.MATCH_DEFAULT_ONLY)
        }
        val pkg = info?.activityInfo?.packageName ?: return null
        // "android" = the chooser / resolver, i.e. several apps and no default: take the first one that handles it
        if (pkg != "android") return pkg
        val all = if (Build.VERSION.SDK_INT >= 33) {
            pm.queryIntentActivities(intent, PackageManager.ResolveInfoFlags.of(PackageManager.MATCH_DEFAULT_ONLY.toLong()))
        } else {
            pm.queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)
        }
        return all.firstOrNull()?.activityInfo?.packageName
    }
}
