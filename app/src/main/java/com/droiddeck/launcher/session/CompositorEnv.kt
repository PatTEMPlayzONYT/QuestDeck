package com.droiddeck.launcher.session

import android.content.Context
import com.droiddeck.launcher.core.FileUtils

/**
 * Compositor switches the session's extra-environment file (droiddeck-env, see
 * [SessionService.envSwitchFile]) can set. The compositor lives in the app process, so the
 * guest environment that file feeds cannot reach it: these are read here, before it starts.
 */
object CompositorEnv {
    const val UBWC = "DROIDDECK_WAYLAND_UBWC"

    /**
     * What the last `DROIDDECK_WAYLAND_UBWC=` line in [lines] says: false for 0 (the compositor
     * advertises LINEAR only, so games hand it linear buffers), true for 1, null when no line
     * gives a 0 or a 1 (the default, compressed buffers on, stays as it is).
     */
    fun ubwc(lines: List<String>): Boolean? {
        var result: Boolean? = null
        for (raw in lines) {
            val line = raw.trim()
            if (!line.startsWith("$UBWC=")) continue
            when (line.substringAfter('=').trim()) {
                "0" -> result = false
                "1" -> result = true
            }
        }
        return result
    }

    fun ubwc(context: Context): Boolean? {
        val file = SessionService.envSwitchFile(context)?.takeIf { it.isFile } ?: return null
        return ubwc(FileUtils.readString(file)?.lines().orEmpty())
    }
}
