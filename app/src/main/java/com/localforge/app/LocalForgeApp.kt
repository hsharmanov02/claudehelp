package com.localforge.app

import android.app.Application
import com.localforge.app.autopilot.Autopilot
import com.localforge.app.autopilot.AutopilotService

/**
 * Holds the app state for the whole process (not per screen), so a reply that's being generated,
 * code that's running or a model download keeps going when you switch to another app.
 */
class LocalForgeApp : Application() {
    val state: AppViewModel by lazy { AppViewModel(this) }

    /** True while LocalForge is on screen (used to decide whether to post a "reply ready" notification). */
    @Volatile var visible = false

    override fun onCreate() {
        super.onCreate()
        if (BuildConfig.AUTOPILOT) {
            Autopilot.init(this)
            // Android restarted the app (not the phone) while a task was running: carry on by itself.
            if (Autopilot.enabled && !Autopilot.awaitingConsent && !AutopilotService.running) AutopilotService.start(this)
        }
    }
}
