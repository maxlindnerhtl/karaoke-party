package com.karaoke.tv.yt

import android.service.notification.NotificationListenerService

/**
 * Does nothing itself. An enabled notification listener is what lets [YtWatchService] call
 * `MediaSessionManager.getActiveSessions()` (and lets it bring the app back to the front on Android 10+).
 * Enabled once via adb, see [YtWatchService.grantCommands] and `scripts/tv_setup.py`:
 * Android 8+: `cmd notification allow_listener <package>/com.karaoke.tv.yt.YtNotificationListener`;
 * Android 7 has no `allow_listener`, so the component is appended to `settings secure enabled_notification_listeners`.
 */
class YtNotificationListener : NotificationListenerService()
