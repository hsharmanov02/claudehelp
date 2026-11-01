package com.localforge.app

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.content.ContextCompat

/**
 * Foreground service that runs while LocalForge is working (thinking, running code, downloading).
 * It shows a notification and keeps the CPU awake so Android doesn't pause or kill the work
 * when you switch apps or turn the screen off.
 */
class WorkService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val text = intent?.getStringExtra(EXTRA_TEXT) ?: "Working…"
        val notification = build(this, text, ongoing = true)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ONGOING_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(ONGOING_ID, notification)
        }
        if (wakeLock == null) {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LocalForge:work").apply {
                setReferenceCounted(false)
                acquire(3 * 60 * 60 * 1000L) // safety cap: 3 hours
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_WORK = "work"
        private const val CHANNEL_DONE = "done"
        private const val ONGOING_ID = 1
        private const val DONE_ID = 2
        private const val EXTRA_TEXT = "text"
        @Volatile private var running = false

        private fun channels(ctx: Context) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL_WORK, "Working in background", NotificationManager.IMPORTANCE_LOW))
            nm.createNotificationChannel(NotificationChannel(CHANNEL_DONE, "Replies ready", NotificationManager.IMPORTANCE_DEFAULT))
        }

        private fun openApp(ctx: Context): PendingIntent = PendingIntent.getActivity(
            ctx, 0,
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        private fun build(ctx: Context, text: String, ongoing: Boolean): Notification {
            channels(ctx)
            return Notification.Builder(ctx, if (ongoing) CHANNEL_WORK else CHANNEL_DONE)
                .setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle("LocalForge")
                .setContentText(text)
                .setContentIntent(openApp(ctx))
                .setOngoing(ongoing)
                .setAutoCancel(!ongoing)
                .setOnlyAlertOnce(true)
                .build()
        }

        /** Starts (or updates the text of) the background-work notification. */
        fun start(ctx: Context, text: String) {
            if (running) {
                // Already in the foreground: just update the notification text.
                ctx.getSystemService(NotificationManager::class.java).notify(ONGOING_ID, build(ctx, text, ongoing = true))
                return
            }
            try {
                ContextCompat.startForegroundService(ctx, Intent(ctx, WorkService::class.java).putExtra(EXTRA_TEXT, text))
                running = true
            } catch (e: Exception) {
                // Android refuses to start foreground work from the background; the work still continues while the app is alive.
            }
        }

        fun stop(ctx: Context) {
            if (!running) return
            running = false
            ctx.stopService(Intent(ctx, WorkService::class.java))
        }

        /** "Your reply is ready" notification, shown when work finishes while you're in another app. */
        fun notifyDone(ctx: Context, text: String) {
            if (Build.VERSION.SDK_INT >= 33 &&
                ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) return
            ctx.getSystemService(NotificationManager::class.java).notify(DONE_ID, build(ctx, text, ongoing = false))
        }
    }
}
