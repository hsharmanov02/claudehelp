package com.localforge.app.autopilot

import android.Manifest
import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.content.ContextCompat
import com.localforge.app.MainActivity
import com.localforge.app.R

/**
 * Keeps LocalForge Autopilot running in the background, 24/7: a foreground service (the ongoing notification)
 * with a CPU wake lock. If Android kills the app anyway, START_STICKY brings the service back and the task
 * carries on. Only the STOP button ends it.
 */
class AutopilotService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null
    private val renew = android.os.Handler(android.os.Looper.getMainLooper())
    private val renewTask = object : Runnable {
        override fun run() {
            wakeLock?.acquire(WAKE_MS)          // re-arm before the timeout runs out
            renew.postDelayed(this, WAKE_MS / 2)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Autopilot.init(this)
        val n = AutopilotNotify.ongoing(this, Autopilot.status)
        if (Build.VERSION.SDK_INT >= 34) startForeground(AutopilotNotify.ONGOING_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        else startForeground(AutopilotNotify.ONGOING_ID, n)
        running = true
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LocalForgeAutopilot:run").apply {
            setReferenceCounted(false)
            acquire(WAKE_MS)
        }
        renew.postDelayed(renewTask, WAKE_MS / 2)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Autopilot.init(this)
        if (intent?.action == ACTION_CONSENT) Autopilot.consent(true)
        if (!Autopilot.enabled) {
            stopSelf()
            return START_NOT_STICKY
        }
        Autopilot.attachLoop()
        Watchdog.schedule(this)
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        // Some phones kill the app when it's swiped away from recents: make sure it comes back.
        if (Autopilot.enabled) Watchdog.schedule(this, soonMs = 60_000)
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        running = false
        renew.removeCallbacks(renewTask)
        Autopilot.detachLoop()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        if (Autopilot.enabled) Watchdog.schedule(this, soonMs = 60_000)
        super.onDestroy()
    }

    companion object {
        const val ACTION_CONSENT = "com.localforge.autopilot.CONSENT"
        private const val WAKE_MS = 60 * 60_000L
        @Volatile var running = false
            private set

        /** Returns false if Android refused (e.g. started from the background without an exemption). */
        fun start(ctx: Context): Boolean = try {
            ContextCompat.startForegroundService(ctx, Intent(ctx, AutopilotService::class.java))
            true
        } catch (e: Exception) {
            false
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, AutopilotService::class.java))
        }

        fun update(ctx: Context, text: String) {
            if (!running) return
            try { ctx.getSystemService(NotificationManager::class.java).notify(AutopilotNotify.ONGOING_ID, AutopilotNotify.ongoing(ctx, text)) } catch (_: Exception) {}
        }
    }
}

object AutopilotNotify {
    const val ONGOING_ID = 41
    const val ASK_ID = 42
    const val ATTENTION_ID = 43
    private const val CH_RUN = "autopilot_run"
    private const val CH_ASK = "autopilot_ask"
    const val EXTRA_AUTOPILOT = "open_autopilot"

    private fun channels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_RUN, "Autopilot running", NotificationManager.IMPORTANCE_LOW))
        nm.createNotificationChannel(NotificationChannel(CH_ASK, "Autopilot needs you", NotificationManager.IMPORTANCE_HIGH))
    }

    fun openApp(ctx: Context, code: Int = 0): PendingIntent = PendingIntent.getActivity(
        ctx, code,
        Intent(ctx, MainActivity::class.java).putExtra(EXTRA_AUTOPILOT, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    )

    fun ongoing(ctx: Context, text: String): Notification {
        channels(ctx)
        val title = if (Autopilot.goal.isNotBlank()) "Autopilot: ${Autopilot.goal.take(60)}" else "LocalForge Autopilot"
        return Notification.Builder(ctx, CH_RUN)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(openApp(ctx))
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun allowed(ctx: Context) = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    /** After a reboot: "Load the model and continue?" with Continue / Not now. */
    fun askConsent(ctx: Context) {
        if (!allowed(ctx)) return
        channels(ctx)
        val yes = PendingIntent.getForegroundService(
            ctx, 1, Intent(ctx, AutopilotService::class.java).setAction(AutopilotService.ACTION_CONSENT),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val no = PendingIntent.getBroadcast(
            ctx, 2, Intent(ctx, AutopilotReceiver::class.java).setAction(AutopilotReceiver.ACTION_NOT_NOW),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val text = "Your phone restarted while this task was running: ${Autopilot.goal.take(120)}\n" +
            "Load the model and continue? Nothing runs until you say so."
        val n = Notification.Builder(ctx, CH_ASK)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("Continue LocalForge Autopilot?")
            .setContentText("Load the model and continue the task?")
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(openApp(ctx, 3))
            .addAction(Notification.Action.Builder(null as android.graphics.drawable.Icon?, "Load model & continue", yes).build())
            .addAction(Notification.Action.Builder(null as android.graphics.drawable.Icon?, "Not now", no).build())
            .setOngoing(true)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(ASK_ID, n)
    }

    fun cancelAsk(ctx: Context) {
        try { ctx.getSystemService(NotificationManager::class.java).cancel(ASK_ID) } catch (_: Exception) {}
    }

    /** Something needs the user: an approval card, or Android stopped the service. */
    fun attention(ctx: Context, text: String) {
        if (!allowed(ctx)) return
        channels(ctx)
        val n = Notification.Builder(ctx, CH_ASK)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("LocalForge Autopilot")
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setContentIntent(openApp(ctx, 4))
            .setAutoCancel(true)
            .build()
        ctx.getSystemService(NotificationManager::class.java).notify(ATTENTION_ID, n)
    }
}

/**
 * Checks every 15 minutes that the autopilot is still alive: restarts the service if Android stopped it,
 * and restarts the loop if it has gone silent (a hung step).
 */
object Watchdog {
    private const val EVERY_MS = 15 * 60_000L
    private const val STALE_MS = 10 * 60_000L

    private fun intent(ctx: Context) = PendingIntent.getBroadcast(
        ctx, 10, Intent(ctx, AutopilotReceiver::class.java).setAction(AutopilotReceiver.ACTION_WATCHDOG),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    )

    fun schedule(ctx: Context, soonMs: Long = EVERY_MS) {
        val am = ctx.getSystemService(AlarmManager::class.java) ?: return
        val at = SystemClock.elapsedRealtime() + soonMs
        try {
            if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent(ctx))
            } else {
                am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent(ctx))
            }
        } catch (_: Exception) {
            try { am.setAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, intent(ctx)) } catch (_: Exception) {}
        }
    }

    fun cancel(ctx: Context) {
        try { ctx.getSystemService(AlarmManager::class.java)?.cancel(intent(ctx)) } catch (_: Exception) {}
    }

    fun check(ctx: Context) {
        Autopilot.init(ctx)
        if (!Autopilot.enabled || Autopilot.awaitingConsent) return
        if (!AutopilotService.running) {
            if (!AutopilotService.start(ctx)) AutopilotNotify.attention(ctx, "Android paused the autopilot. Tap to carry on.")
        } else if (!Autopilot.loopAlive) {
            Autopilot.attachLoop()
        } else if (System.currentTimeMillis() - Autopilot.lastBeat > STALE_MS) {
            Autopilot.restartLoop()
        }
        schedule(ctx)
    }
}

/** Phone restarted, app updated, the watchdog alarm, and the "Not now" button. */
class AutopilotReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        Autopilot.init(ctx)
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, "android.intent.action.QUICKBOOT_POWERON" -> {
                // Never load the model on its own after a reboot: ask first, do nothing until the user answers.
                if (Autopilot.awaitingConsent) AutopilotNotify.askConsent(ctx)
            }
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                if (Autopilot.awaitingConsent) AutopilotNotify.askConsent(ctx)
                else if (Autopilot.enabled) AutopilotService.start(ctx)
            }
            ACTION_NOT_NOW -> Autopilot.consent(false)
            ACTION_WATCHDOG -> Watchdog.check(ctx)
        }
    }

    companion object {
        const val ACTION_NOT_NOW = "com.localforge.autopilot.NOT_NOW"
        const val ACTION_WATCHDOG = "com.localforge.autopilot.WATCHDOG"
    }
}
