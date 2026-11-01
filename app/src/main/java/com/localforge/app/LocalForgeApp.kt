package com.localforge.app

import android.app.Application

/**
 * Holds the app state for the whole process (not per screen), so a reply that's being generated,
 * code that's running or a model download keeps going when you switch to another app.
 */
class LocalForgeApp : Application() {
    val state: AppViewModel by lazy { AppViewModel(this) }

    /** True while LocalForge is on screen (used to decide whether to post a "reply ready" notification). */
    @Volatile var visible = false
}
