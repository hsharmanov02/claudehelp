package com.localforge.app.agent

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.atomic.AtomicInteger

/**
 * Runs commands inside Termux (a free Linux environment for Android) using its RUN_COMMAND API,
 * which unlocks compilers and runtimes for C, C++, Java, Go, Rust, Ruby, Node and more.
 */
object TermuxBridge {
    const val PACKAGE = "com.termux"
    const val PERMISSION = "com.termux.permission.RUN_COMMAND"
    private const val BASH = "/data/data/com.termux/files/usr/bin/bash"
    private val counter = AtomicInteger(0)

    /** One-time setup to paste into Termux. */
    const val SETUP_COMMAND =
        "termux-setup-storage; mkdir -p ~/.termux && echo \"allow-external-apps=true\" >> ~/.termux/termux.properties && " +
            "termux-reload-settings; pkg update -y && pkg install -y clang golang rust openjdk-21 kotlin ruby php nodejs lua54 perl"

    fun isInstalled(ctx: Context): Boolean = try {
        ctx.packageManager.getPackageInfo(PACKAGE, 0)
        true
    } catch (e: PackageManager.NameNotFoundException) {
        false
    }

    fun hasPermission(ctx: Context): Boolean =
        ContextCompat.checkSelfPermission(ctx, PERMISSION) == PackageManager.PERMISSION_GRANTED

    fun isReady(ctx: Context): Boolean = isInstalled(ctx) && hasPermission(ctx)

    data class Result(val exitCode: Int, val stdout: String, val stderr: String, val error: String?)

    /** Runs [script] with bash inside Termux and waits for the output. Returns null on timeout. */
    suspend fun run(ctx: Context, script: String, workDir: String, timeoutMs: Long): Result? {
        val app = ctx.applicationContext
        val action = "${app.packageName}.TERMUX_RESULT_${counter.incrementAndGet()}"
        val done = CompletableDeferred<Result>()
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, intent: Intent) {
                val b: Bundle? = intent.getBundleExtra("result")
                if (b == null) {
                    done.complete(Result(-1, "", "", "Termux returned no result"))
                    return
                }
                val errCode = b.getInt("err", -1)
                val errMsg = b.getString("errmsg")
                done.complete(
                    Result(
                        exitCode = b.getInt("exitCode", -1),
                        stdout = b.getString("stdout") ?: "",
                        stderr = b.getString("stderr") ?: "",
                        error = if (errCode > 0 && !errMsg.isNullOrBlank()) errMsg else null
                    )
                )
            }
        }
        ContextCompat.registerReceiver(app, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        try {
            val resultIntent = Intent(action).setPackage(app.packageName)
            val pi = PendingIntent.getBroadcast(
                app, counter.get(), resultIntent,
                PendingIntent.FLAG_ONE_SHOT or PendingIntent.FLAG_MUTABLE
            )
            val intent = Intent("com.termux.RUN_COMMAND").apply {
                setClassName(PACKAGE, "com.termux.app.RunCommandService")
                putExtra("com.termux.RUN_COMMAND_PATH", BASH)
                putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arrayOf("-lc", script))
                putExtra("com.termux.RUN_COMMAND_WORKDIR", workDir)
                putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
                putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pi)
            }
            app.startService(intent)
            return withTimeoutOrNull(timeoutMs) { done.await() }
        } finally {
            try { app.unregisterReceiver(receiver) } catch (_: Exception) {}
        }
    }
}
